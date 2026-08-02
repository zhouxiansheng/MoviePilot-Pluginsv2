# APNG 逐帧量化压缩集成计划

**Goal:** 将 pngquant + apngopt 集成到 MoviePilot Fork 插件
**Architecture:** FFmpeg生成原始APNG -> pngquant逐帧量化 -> apngopt无损优化
**Tech Stack:** Python 3, pngquant 2.10.1 APNG-patched, APNG Optimizer 1.4, FFmpeg

## 关键约束

- 运行环境：群晖Docker MoviePilot容器 Linux x86_64
- 二进制必须是Linux版 随插件打包
- 必须向后兼容：旧值off/medium/strong自动映射
- GIF格式不受影响 继续用FFmpeg
- pngquant必须用iSparta修补版 支持APNG逐帧处理
- 二进制总大小约661KB
## 涉及文件

| 文件 | 修改 |
|------|------|
| __init__.py | UI VSelect改数值输入 配置 向后兼容 |
| style_animated_1~4.py | APNG编码段 |
| utils/apng_compressor.py | 新建 pngquant+apngopt封装 |
| bin/apngquant | 新建 Linux x86_64二进制 |
| bin/apngopt | 新建 Linux x86_64二进制 |

---

## Task 1: 添加Linux二进制文件

- 从iSparta GitHub下载Linux版apngquant和apngopt
- 放入插件bin/目录
- 验证文件大小 apngquant约345KB apngopt约316KB

---

## Task 2: 创建APNG压缩模块 utils/apng_compressor.py

核心函数 compress_apng input_path output_path quality=80 use_apngopt=True

流程：
1. quality>0时 apngquant --quality=0-{value} --force 逐帧量化
2. apngopt -z2 无损优化 7-zip压缩
3. 任何步骤失败则回退到FFmpeg原始输出

关键点：
- _ensure_executable Docker中可能丢失执行权限 需chmod
- _get_binary 检测二进制是否存在
- 超时300秒 异常捕获

---

## Task 3: 修改4个style文件

每个文件的APNG编码段else分支改为：
1. FFmpeg始终用off模式rgba生成原始APNG
2. 如果quality>0 调用compress_apng后处理
3. 压缩失败时warning并保留FFmpeg原始输出

旧值映射：off->0 medium->60 strong->40 数值->数值

---

## Task 4: 修改UI和向后兼容 __init__.py

- UI VSelect改为VTextField type=number 0-100
- 配置读取 旧值off/medium/strong映射到0/60/40
- 默认值改为80
- 配置保存为数值类型

---

## Task 5: 测试验证

- 本地验证模块导入
- Docker中验证二进制可执行
- 不同质量值0/40/60/80/100生成测试
- 旧配置兼容测试

---

## 风险与应对

| 风险 | 应对 |
|------|------|
| Linux二进制需要共享库 | 捕获异常 回退FFmpeg |
| ARM架构不兼容 | 检测架构 回退FFmpeg |
| 超时 | 300秒超时 回退FFmpeg |
| 旧配置不兼容 | Task4已处理 |
| chmod失败 | 尝试chmod 失败回退 |
