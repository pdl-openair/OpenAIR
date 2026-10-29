# Method — Reproducibility

<!-- substage: reproducibility
     确保实验可复现：环境锁定、种子固定、版本控制、数据可访问性。
     产出汇入 experiment_design.md 的「Reproroducibility Assurance」部分。 -->

## 运行环境 (Environment)

### 硬件 (Hardware)

| Resource | Specification |
|----------|--------------|
| GPU | <!-- e.g. NVIDIA A100 80GB x 2 --> |
| CPU | <!-- --> |
| RAM | <!-- --> |
| Disk | <!-- --> |

### 软件 (Software)

| Component | Version |
|-----------|---------|
| OS | <!-- e.g. Ubuntu 22.04 --> |
| Runtime | <!-- e.g. Python 3.11 --> |
| Framework | <!-- e.g. PyTorch 2.1.0 --> |
| CUDA | <!-- e.g. 12.1 --> |

## 随机种子 (Random Seeds)

<!-- 固定所有随机源以确保可复现。 -->

| Seed Scope | Value | Library / Framework |
|-----------|-------|-------------------|
| global | <!-- e.g. 42 --> | <!-- numpy, random, torch, etc. --> |
| <!-- additional --> | <!-- --> | <!-- --> |

- All ablation and baseline runs use the SAME seed set.

## 版本锁定 (Version Pins)

<!-- 完整依赖版本列表。 -->

| Package | Version | Pinned In |
|---------|---------|----------|
| <!-- --> | <!-- --> | requirements.txt / pyproject.toml / environment.yml |

## 容器规格 (Container Spec)

<!-- Docker / Singularity 镜像定义。 -->

- **Image:** <!-- e.g. pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime -->
- **Dockerfile:** <!-- path or inline -->
- **Build command:** <!-- -->

## 数据访问说明 (Data Access Notes)

<!-- 数据获取方式、下载脚本、访问权限。 -->

| Dataset | Access Method | Auth Required | Download Script |
|---------|--------------|--------------|----------------|
| <!-- --> | public_url / API / internal / upon_request | yes / no | <!-- path --> |
