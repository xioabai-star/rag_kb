"""块1 收尾：从原始 Qwen3-Reranker-0.6B 导出 ONNX，做 per-channel 动态 int8 量化。

在隔离环境运行（不污染生产依赖）：
  uv run --with "optimum[onnxruntime]" --with transformers==4.57.6 -- python scripts/export_reranker_onnx.py

产出：
  fp32:  D:/code/ai_demo/models/qwen3-reranker-0.6b-onnx-fp32/
  int8:  D:/code/ai_demo/models/qwen3-reranker-0.6b-onnx-int8-perchannel/
"""

from pathlib import Path

from optimum.onnxruntime import ORTModelForCausalLM, ORTQuantizer
from optimum.onnxruntime.configuration import AutoQuantizationConfig
from transformers import AutoTokenizer

SRC = "D:/code/ai_demo/models/Qwen3-Reranker-0.6B"
FP32_DIR = Path("D:/code/ai_demo/models/qwen3-reranker-0.6b-onnx-fp32")
INT8_DIR = Path("D:/code/ai_demo/models/qwen3-reranker-0.6b-onnx-int8-perchannel")

# 1. 导出 fp32（CausalLM 路径：无分类头，text-generation 任务，无需 KV cache）
model = ORTModelForCausalLM.from_pretrained(SRC, export=True, use_cache=False)
model.save_pretrained(FP32_DIR)
tok = AutoTokenizer.from_pretrained(SRC)
tok.save_pretrained(FP32_DIR)
print("fp32 导出完成:", FP32_DIR)

# 2. per-channel 动态 int8 量化（avx2 档，本机 i7-13620H 无 AVX512）
quantizer = ORTQuantizer.from_pretrained(FP32_DIR)
dq_config = AutoQuantizationConfig.avx2(is_static=False)  # 动态 = 激活不量化，权重 per-channel int8
quantizer.quantize(save_dir=INT8_DIR, quantization_config=dq_config)
print("int8 量化完成:", INT8_DIR)
