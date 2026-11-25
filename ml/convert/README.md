# BPU model conversion / BPU 模型转换

Converts immich's ONNX models (`buffalo_l` face detection + recognition, `ViT-B-32__openai` visual) into
RDK X5 BPU model packs. Prebuilt packs are in the Releases; this is only needed for other model versions.
把 immich 的 ONNX 模型转换成 RDK X5 BPU 模型包。Releases 里已有预构建的包，只有换模型时才需要。

Requirements / 需要：

- x86-64 Linux host, ≥8 GB RAM, conda Python 3.10 env `oe_env` (onnx 1.12, numpy 1.23, protobuf 3.19.4, torch)
- D-Robotics OE 1.2.8 toolchain for X5 (`hb_mapper` 1.24.3, horizon_nn 1.1.0 patch, hbdk 3.49.15), installed
  into a venv `OE_VENV` on top of `oe_env`; get it from the D-Robotics developer site
  / 从地瓜机器人开发者网站获取 OE 1.2.8
- onnxruntime 1.16 for the preprocessing tools: `pip install --target pylib onnxruntime==1.16.3`
- calibration images: WIDER FACE val, ~100 COCO val2017 images

```sh
mkdir -p work/onnx
docker exec immich_machine_learning tar -C /cache -cf - \
	facial-recognition/buffalo_l clip/ViT-B-32__openai | tar -C work/onnx -xf -
PYTHONPATH=$PWD/pylib OE_VENV=/path/to/oe128_venv \
	sh build_models.sh work <WIDER_val dir> <COCO dir>
# -> work/pack/<task>/<model>/<type>/bpu/x5/{model.json,*.bin}, about 1 hour
```

Accuracy check (do it after every conversion) / 每次转换后检查精度：
`make_ref.py` builds an eval set with the ONNX fp32 outputs on the host, `../tools/eval_bpu.py` runs it on the
board. Pass criteria: face recognition / CLIP mean cosine ≥ 0.99, face detection recall ≥ 0.99 at threshold 0.7.
Why int16 + `max` calibration, the PRelu rewrite and the input layout matter: see `docs/*/design.md` §5.
