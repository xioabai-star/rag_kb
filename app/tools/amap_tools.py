"""高德地图 MCP 工具（模块 13）：把外部世界接进问答图。

AmapTools：MCP 客户端会话（懒加载单例）+ 两个工具——
route_plan（地理编码→路线规划显式链）与 weather（单步调用）。
内部工具（报销）自研直连；外部工具走 MCP 标准协议（架构取舍见 README）。
"""
import json
import logging
import threading

import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app import config


logger = logging.getLogger(__name__)
AMAP_MCP_URL = "https://mcp.amap.com/mcp?key={key}"


class AmapTools:
    """高德地图 MCP 客户端（懒加载单例 + 会话复用）：对外提供 route_plan 与 weather 两个工具"""
    def __init__(self):
        self._session = None            # MCP 会话（第一个调用时创建）
        self._lock = asyncio.Lock()     # 串行化保护（防并发写乱共享连接）
        self._ctx = None                # 存连接对象，作废时好关闭（资源清理）
        logger.info("AmapTools 实例已创建")

    async def _get_session(self) -> ClientSession:
        """没会话就建一个（调用方已拿到锁，这里不用加锁）"""
        if self._session is None:
            if not config.AMAP_KEY:
                raise RuntimeError("高德地图(AMAP_KEY) API 密钥未配置，请添加环境变量 AMAP_KEY")
            ctx = streamable_http_client(AMAP_MCP_URL.format(key = config.AMAP_KEY))
            read, write = await ctx.__aenter__() # 手动进入：进入后不退，会话长期存活
            session = ClientSession(read, write)
            await session.__aenter__() #2.x 进入即完成协议握手（实测无需再 initialize）
            self._ctx, self._session = ctx, session
            logger.info("高德 MCP 会话已建立")
        return self._session


    async def _discard_session(self) -> None:
        """作废旧会话：尽力关闭连接 + 清引用。 下次调用自动重建（断线自愈）"""
        ctx, session = self._ctx, self._session
        self._ctx,self._session = None, None
        for closer in (session, ctx):
            if closer is not None:
                try:
                    await closer.__aexit__(None, None, None)
                except Exception as e:
                    pass
        logger.warning("高德 MCP 会话已作废，下次调用自动重建")

    async def aclose(self) -> None:
        """
        显式关闭会话（FastAPI shutdown / 脚本收尾时调用）。
        没有这个出口，程序结束时连接会被垃圾回收在"错误时机"清理（事件循环已关），
        表现为脚本退出时报 cancel scope 错——资源要管到程序结束。
        """
        async with self._lock:
            await self._discard_session()


    async def _call(self, tool_name: str, args: dict) -> str:
        """一次 MCP 调用： 锁内 拿会话 -> 调用 -> 结果文本化"""
        async with self._lock:
            session = await self._get_session()
            try:
                result = await session.call_tool(tool_name, args)
                parts = [getattr(b, "text", str(b)) for b in result.content]
                return "\n".join(p for p in parts if p)
            except Exception as e:
                logger.error(f"MCP 调用 {tool_name} 失败：{e}", exc_info=True)
                await self._discard_session()
                raise

    # ---------- 工具 1：出行路线（显式链：地名 → 坐标 → 公交方案） ----------
    async def route_plan(self, origin: str, destination: str) -> str:
        """
        链式编排： geo（起点） -> geo（终点） -> transit（路线规划）
        geo 返回里自带城市，自动填跨城参数（工具喂工具的活实例）。
        业务失败（地名查不到/高德报错）→ 返回话术文本，不抛异常：
        generate_tool 总要有话可说——抛异常会让整条 SSE 流挂 error 帧。
        """
        try:
            geo1 = json.loads(await self._call("maps_geo",{"address": origin})) #json.load 是把json文本解析成python对象，这里是解析成dict了
            geo2 = json.loads(await self._call("maps_geo",{"address": destination}))
            r1, r2 = geo1["results"][0], geo2["results"][0]  # 取第一个候选
            loc1, loc2 = r1["location"], r2["location"]  # "经度,纬度"
            city1 = r1.get("city") or r1.get("province")  # 城市参数（同城两次传同一值）
            city2 = r2.get("city") or r2.get("province")
            plan = await self._call(
                "maps_direction_transit_integrated",
                {"origin": loc1, "destination": loc2, "city": city1, "cityd": city2},
            )
            return(
                f"起点： {origin}（{loc1}）\n"
                f"终点： {destination}（{loc2}）\n\n"
                f"路线规划：\n{plan}"
            )
        except Exception as e:
            logger.warning(f"出行路线查询失败：{origin} -> {destination}: {e}")
            return f"路线查询失败（{e}）。请把出发地和目的地说得更具体（含城市），再试一次。"

    async def weather(self, city: str) -> str:
        """单步工具：查城市天气（maps_weather 支持城市名）。失败同样转话术。"""
        try:
            return await self._call("maps_weather", {"city": city})
        except Exception as e:
            logger.warning(f"天气查询失败：{city}: {e}")
            return f"天气查询失败（{e}）。请把城市说具体点，再试一次。"


# ==================== 进程级单例 ====================
# 与 reranker 同款模式：模块级锁 + 双重检查（防并发首次创建重复建连接）
_instance: AmapTools | None = None   # 模块级单例
_lock = threading.Lock()


def get_amap_tools() -> AmapTools:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = AmapTools()
    return _instance