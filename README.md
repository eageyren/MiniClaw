# miniclaw

`miniclaw` 是一个基于 Typer 的命令行智能代理。它使用已配置的聊天模型，通过结构化工具检查和修改指定的工作区。

## 安装与配置

项目使用 Python 虚拟环境 `miniclaw`。激活虚拟环境后，在项目根目录执行：

```text
python -m pip install -e ".[dev]"
copy .env.example .env
```

然后编辑 `.env`，至少配置以下两个变量：

```dotenv
OPENAI_API_KEY=你的 API Key
OPENAI_MODEL=你的模型名称
```

模型名称必须通过 `OPENAI_MODEL` 配置，项目不会在代码中硬编码默认模型。

如果使用 OpenAI 兼容接口，还可以配置：

```dotenv
OPENAI_BASE_URL=https://你的接口地址/v1
OPENAI_TEMPERATURE=0
```

## 使用方法

基本用法：

```text
miniclaw "列出所有 Python 文件并总结它们"
```

指定工作区：

```text
miniclaw "创建一个 notes.txt 文件" --workspace C:\\work\\agent-space
```

查看帮助：

```text
miniclaw --help
python -m miniclaw --help
```

### 默认工作区

如果没有传入 `--workspace`，`miniclaw` 会在当前目录下自动创建并使用：

```text
.miniclaw-workspace/
```

文件工具和搜索工具会拒绝解析到工作区之外的路径，包括通过符号链接逃逸到外部的路径。

## 工具说明

- `FileRead`：读取工作区内的 UTF-8 文件，可限制起始行和读取行数，并返回行号。
- `FileWrite`：创建或覆盖工作区内的 UTF-8 文件，同时自动创建缺失的父目录。
- `FileEdit`：替换文件中的一段文本。只有当旧文本恰好匹配一次时才会写回文件。
- `Grep`：使用正则表达式搜索文件，支持 glob 文件过滤、忽略大小写和结果数量限制。
- `Bash`：在工作区目录下执行命令，并提供超时控制。

CLI 会通过 ReAct 循环持续运行模型与工具调用，实时显示工具名称、参数和结果，直到模型返回最终回答，或者达到默认最多 10 轮模型调用。模型遵循 actor prompt：在工作区内完成任务，编辑前先读取文件，并在结尾总结修改和命令。

## Bash 安全说明

`Bash` 工具会将工作区设置为子进程的当前工作目录，并提供超时控制，但它**不是操作系统级别的沙箱**。

命令仍可能利用自身权限，通过绝对路径、环境变量或其他程序访问工作区之外的文件。因此，不要使用 `Bash` 执行不可信的任务或命令。

## 路径安全

所有文件和搜索路径都会经过工作区校验：

- 拒绝 `..` 导致的路径穿越；
- 拒绝位于工作区之外的绝对路径；
- 检查已存在的符号链接和父目录符号链接；
- 防止通过相似前缀目录绕过检查；
- `FileEdit` 在匹配不唯一或找不到文本时不会修改文件。

## 开发与测试

运行测试：

```text
python -m pytest -q
```

以可编辑模式安装项目：

```text
python -m pip install -e .
```

也可以先编译检查 Python 文件：

```text
python -m compileall -q src tests
```

## 注意事项

不要将以下内容提交到版本库：

- `.env` 文件；
- 真实的 API Key；
- `.venv` 虚拟环境目录；
- 测试缓存和构建产物。
