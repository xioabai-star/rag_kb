<script setup>
// 登录/注册页：浅色企业版——背景 = 登录后主界面的同款浅灰（#f5f6f8），进入系统时画面无缝衔接。
// 无任何半透明/毛玻璃叠层（毛边根除），实心白卡孤立居中；装饰靠出血几何与水印，不占版心。
// 两种模式共用一个表单；提交逻辑走 auth.js（登录=表单、注册=JSON——两接口格式不同）。
import { ref } from 'vue'
import { login, register } from '../auth'

const emit = defineEmits(['authed'])

const mode = ref('login') // 'login' 登录 | 'register' 注册
const username = ref('')
const password = ref('')
const errorMsg = ref('')
const loading = ref(false)

const switchMode = () => {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  errorMsg.value = ''
}

const submit = async () => {
  if (!username.value.trim() || !password.value) {
    errorMsg.value = '用户名和密码不能为空'
    return
  }
  errorMsg.value = ''
  loading.value = true
  try {
    if (mode.value === 'login') {
      const user = await login(username.value, password.value)
      emit('authed', user)
    } else {
      await register(username.value, password.value) // 注册只建账号
      const user = await login(username.value, password.value) // 前端补一次登录，免去再手输一遍
      emit('authed', user)
    }
  } catch (e) {
    errorMsg.value = e.message || '操作失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <!-- 背景装饰：出血几何 + 品牌水印，全部藏在卡片身后（指针穿透、不占版心） -->
    <div class="deco deco-top-ring"></div>
    <div class="deco deco-bottom-ring"></div>
    <div class="deco deco-dots"><i></i><i></i><i></i></div>
    <span class="deco watermark">知</span>

    <!-- 顶栏：品牌 -->
    <header class="topbar">
      <span class="mini-logo">知</span>
      <span class="mini-name">企业知识库问答系统</span>
    </header>

    <!-- 中部：登录卡 -->
    <main class="stage">
      <div class="card">
        <div class="head">
          <div class="logo">知</div>
          <h1>企业知识库问答</h1>
          <p class="sub">
            {{ mode === 'login' ? '欢迎回来，登录后继续提问' : '创建账号，加入企业知识库' }}
          </p>
        </div>

        <form class="form" @submit.prevent="submit">
          <div class="field">
            <label for="u">用户名</label>
            <div class="input-wrap">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                <circle cx="12" cy="7" r="4" />
              </svg>
              <input
                id="u"
                v-model="username"
                type="text"
                autocomplete="username"
                placeholder="请输入用户名"
              />
            </div>
          </div>

          <div class="field">
            <label for="p">密码</label>
            <div class="input-wrap">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="3" y="11" width="18" height="11" rx="2" />
                <path d="M7 11V7a5 5 0 0 1 10 0v4" />
              </svg>
              <input
                id="p"
                v-model="password"
                type="password"
                autocomplete="current-password"
                placeholder="请输入密码"
              />
            </div>
          </div>

          <p v-if="errorMsg" class="error">{{ errorMsg }}</p>

          <button type="submit" class="submit-btn" :disabled="loading">
            {{ loading ? '请稍候…' : mode === 'login' ? '登 录' : '注册并登录' }}
          </button>
        </form>

        <p class="switch">
          <template v-if="mode === 'login'">
            还没有账号？<a href="#" @click.prevent="switchMode">立即注册</a>
          </template>
          <template v-else>
            已有账号？<a href="#" @click.prevent="switchMode">返回登录</a>
          </template>
        </p>

        <div class="card-foot">
          <span class="trust">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="3" y="11" width="18" height="11" rx="2" />
              <path d="M7 11V7a5 5 0 0 1 10 0v4" />
            </svg>
            企业级加密 · JWT 鉴权
          </span>
          <span class="demo">演示账号 admin / admin123</span>
        </div>
      </div>
    </main>

    <!-- 底栏 -->
    <footer class="foot">RAG 企业知识库 · FastAPI + LangGraph + 混合检索 · 演示环境</footer>
  </div>
</template>

<style scoped>
/* 背景与主界面同色系：#f5f6f8 —— 登录后画面不跳变 */
.login-page {
  position: relative;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-family: 'PingFang SC', 'Microsoft YaHei', -apple-system, sans-serif;
  -webkit-font-smoothing: antialiased;
  background: #f5f6f8;
}

/* ---------- 背景装饰（纯色描边几何：无半透明叠层 → 无毛边） ---------- */
.deco { position: absolute; pointer-events: none; }
.deco-top-ring {
  top: -150px;
  right: -110px;
  width: 380px;
  height: 380px;
  border: 52px solid #e4ecfb;
  border-radius: 50%;
  box-sizing: border-box;
}
.deco-bottom-ring {
  left: -170px;
  bottom: -200px;
  width: 440px;
  height: 440px;
  border: 62px solid #e9effb;
  border-radius: 50%;
  box-sizing: border-box;
}
.deco-dots {
  top: 120px;
  right: 6%;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: flex-end;
}
.deco-dots i {
  display: block;
  border-radius: 50%;
  background: #dbe6f9;
}
.deco-dots i:nth-child(1) { width: 10px; height: 10px; background: #c9dcf9; }
.deco-dots i:nth-child(2) { width: 16px; height: 16px; }
.deco-dots i:nth-child(3) { width: 6px; height: 6px; }
.watermark {
  right: 4%;
  bottom: 12px;
  font-size: 210px;
  font-weight: 700;
  line-height: 1;
  color: #e9edf4;
  user-select: none;
  z-index: 0;
}

/* ---------- 顶栏品牌 ---------- */
.topbar {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 22px 0 0;
}
.mini-logo {
  width: 24px;
  height: 24px;
  border-radius: 7px;
  background: linear-gradient(150deg, #1d4ed8, #2563eb);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}
.mini-name { font-size: 14px; font-weight: 600; color: #4e5969; letter-spacing: 1px; }

/* ---------- 中部：登录卡 ---------- */
.stage {
  position: relative;
  z-index: 1;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 0;
}
.card {
  width: 400px;
  max-width: calc(100vw - 40px);
  box-sizing: border-box;
  padding: 36px 38px 22px;
  background: #fff;
  border: 1px solid #eef0f3;
  border-radius: 16px;
  box-shadow: 0 16px 48px rgba(31, 35, 41, 0.07);
  text-align: center;
  animation: rise 0.4s ease both;
}
@keyframes rise {
  from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: translateY(0); }
}

.head { margin-bottom: 24px; }
.logo {
  width: 48px;
  height: 48px;
  margin: 0 auto 14px;
  border-radius: 13px;
  background: linear-gradient(150deg, #1d4ed8, #2563eb);
  color: #fff;
  font-size: 23px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 8px 20px rgba(37, 99, 235, 0.25);
}
.head h1 { font-size: 20px; color: #1f2329; margin: 0 0 8px; letter-spacing: 1px; }
.head .sub { font-size: 13px; color: #86909c; margin: 0; }

.form { text-align: left; }
.field { margin-bottom: 14px; }
.field label { display: block; font-size: 13px; color: #4e5969; margin-bottom: 6px; }
.input-wrap { position: relative; }
.input-wrap svg {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  width: 16px;
  height: 16px;
  color: #a8abb2;
}
.input-wrap input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #e5e7eb;
  border-radius: 9px;
  padding: 10px 12px 10px 38px;
  font: inherit;
  font-size: 14px;
  background: #fafbfc;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s, background 0.15s;
}
.input-wrap input:focus {
  border-color: #2563eb;
  background: #fff;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
}
.input-wrap input::placeholder { color: #a8abb2; }

.error { color: #dc2626; font-size: 13px; margin: 2px 0 12px; }
.submit-btn {
  width: 100%;
  background: linear-gradient(150deg, #1d4ed8, #2563eb);
  color: #fff;
  border-radius: 9px;
  padding: 11px 0;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 4px;
  margin-top: 4px;
  transition: filter 0.15s;
}
.submit-btn:hover { filter: brightness(1.08); }
.submit-btn:disabled { background: #c7d2fe; cursor: not-allowed; filter: none; }

.switch { text-align: center; font-size: 13px; color: #86909c; margin-top: 16px; }
.switch a { color: #2563eb; text-decoration: none; font-weight: 500; }
.switch a:hover { text-decoration: underline; }

.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid #f0f1f3;
  font-size: 11.5px;
  color: #a8abb2;
}
.trust { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.trust svg { width: 12px; height: 12px; color: #9ca3af; }
.demo { white-space: nowrap; }

/* ---------- 底栏 ---------- */
.foot {
  position: relative;
  z-index: 1;
  text-align: center;
  padding: 0 0 18px;
  font-size: 12px;
  letter-spacing: 0.5px;
  color: #c0c6cf;
}

/* 窄屏：收掉出血装饰，防止与卡片相挤 */
@media (max-width: 640px) {
  .deco { display: none; }
}
</style>
