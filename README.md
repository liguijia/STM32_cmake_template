# STM32 CMake 项目模板

适用于 STM32CubeMX 生成的 CMake 工程，在 VS Code 中实现一键编译、烧录与调试。

---

## 推荐工作流

本模板设计为**每台机器 clone 一次，所有项目共享工具链**：

```
D:\Codes\
├── stm32_cmake_template\   ← clone 一次，运行 make setup 下载工具
├── my_project_1\           ← make new-project NAME=my_project_1 创建
├── my_project_2\           ← make new-project NAME=my_project_2 创建
└── ...
```

新项目通过 `env.mk` 重定向指向模板中已下载的工具链和 OpenOCD，**无需重复下载**。

---

## 前置条件

| 工具 | 最低版本 | 说明 |
|------|----------|------|
| [Python](https://www.python.org/downloads/) | 3.10 | 加入 PATH |
| [CMake](https://cmake.org/download/) | 3.25 | 加入 PATH |
| [Ninja](https://github.com/ninja-build/ninja/releases) | 1.11 | 加入 PATH |
| [Make](https://www.gnu.org/software/make/) | 4.0 | 加入 PATH |
| [Git](https://git-scm.com/) | 任意 | 可选，用于版本管理 |
| [VS Code](https://code.visualstudio.com/) | 任意 | 推荐安装以下扩展 |

### Windows（推荐使用 Scoop）

[Scoop](https://scoop.sh/) 是 Windows 下的命令行包管理器，一行命令安装所有依赖，自动配置 PATH：

```powershell
# 安装 Scoop（如尚未安装，在 PowerShell 中执行）
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
Invoke-RestMethod -Uri https://get.scoop.sh | Invoke-Expression

# 安装前置工具
scoop install python cmake ninja make git
```

安装完成后在任意终端中即可直接使用，无需手动配置环境变量。

> 也可以手动从上方链接逐个下载安装，但需自行将每个工具加入系统 PATH。

### Linux

大部分依赖可通过系统包管理器安装：

```bash
# Debian / Ubuntu
sudo apt update
sudo apt install python3 python3-pip cmake ninja-build make git

# Arch Linux
sudo pacman -S python cmake ninja make git

# Fedora
sudo dnf install python3 cmake ninja-build make git
```

注意事项：

- **Python 命令名**：部分发行版中 `python` 指向 Python 2 或不存在，需确保 `python` 命令指向 Python 3。可通过 `sudo apt install python-is-python3`（Debian/Ubuntu）或创建别名解决。
- **CMake 版本**：Ubuntu 22.04 LTS 仓库中的 CMake 版本为 3.22，低于要求的 3.25。可通过 [Kitware APT 仓库](https://apt.kitware.com/) 或 `pip install cmake` 获取新版。
- **串口权限**：烧录/调试需要访问 `/dev/ttyACM*` 或 `/dev/ttyUSB*`，需将用户加入 `dialout` 组：
  ```bash
  sudo usermod -aG dialout $USER
  # 重新登录后生效
  ```
- **udev 规则**：ST-Link / DAPLink / J-Link 等调试器需要 udev 规则才能免 root 使用。OpenOCD 和 J-Link 安装包通常附带规则文件，也可手动添加：
  ```bash
  # OpenOCD（安装后通常已自带）
  sudo cp /usr/share/openocd/contrib/60-openocd.rules /etc/udev/rules.d/
  sudo udevadm control --reload-rules
  ```

### VS Code 扩展

在 VS Code 中按 `Ctrl+Shift+X` 搜索安装：

- `marus25.cortex-debug` — 调试器前端（Cortex-Debug）
- `llvm-vs-code-extensions.vscode-clangd` — C/C++ 智能提示
- `ms-vscode.cmake-tools` — CMake 支持（可选）

---

## 快速开始

### 第一步：clone 模板并初始化工具（每台机器只需一次）

```bash
git clone https://github.com/your-name/stm32_cmake_template
cd stm32_cmake_template
make setup
```

`make setup` 会下载工具链、OpenOCD、安装 pyOCD pack 等，完成后**工具永久保留**在 `tools/` 目录中，后续所有新项目共享。

### 第二步：创建新项目

在 `stm32_cmake_template` 目录中运行：

```bash
make new-project NAME=my_blinky
```

或在 VS Code 中按 `Ctrl+Shift+P` → `Tasks: Run Task` → **new project**（会弹出输入框）。

脚本会在同级目录创建 `../my_blinky/`，并生成指向模板工具的 `env.mk`，**无需重新下载任何工具**。

### 第三步：用 CubeMX 生成代码到新项目目录

1. 打开 STM32CubeMX，配置 MCU
2. Project Manager → **Toolchain/IDE 选择 CMake**
3. Project Location 设为 `../my_blinky`（与模板同级）
4. 点击 **GENERATE CODE**

### 第四步：在 VS Code 中打开并完成配置

```bash
code ../my_blinky/my_blinky.code-workspace
```

在 VS Code 终端中运行（仅需一次）：

```bash
make gen-openocd-cfg   # 生成 .openocd/target.cfg，下载 SVD，更新 launch.json
```

然后：
- `Ctrl+Shift+B` 编译
- `F5` 调试

---

## 用户代码与 CubeMX 代码分离

模板将用户代码和 CubeMX 生成的代码分离到不同目录，避免 CubeMX 重新生成时覆盖用户代码：

```
your-project/
├── user/                    ← 你的应用代码（不会被 CubeMX 覆盖）
│   ├── Src/                   放 .c/.cpp/.s 源文件
│   ├── Inc/                   放 .h/.hpp 头文件
│   └── user_sources.cmake     自动收集 user/ 下所有源文件
├── Core/                    ← CubeMX 生成：main.c、中断处理、外设初始化
├── Drivers/                 ← CubeMX 生成：HAL 库 / CMSIS
├── Middlewares/             ← CubeMX 生成：中间件（如有）
└── CMakeLists.txt           ← CubeMX 生成
```

### 自动编译

`user/` 目录下的源文件**自动参与编译**，无需手动修改 `CMakeLists.txt`：

- `make configure`（或 `make`）时，Makefile 自动在 CubeMX 生成的 `CMakeLists.txt` 末尾注入 `include(user/user_sources.cmake OPTIONAL)`
- 该注入是幂等的：已注入则跳过，CubeMX 重新生成后自动重新注入
- `user_sources.cmake` 使用 `file(GLOB_RECURSE ... CONFIGURE_DEPENDS)` 递归收集 `user/Src/` 和 `user/Inc/` 下的所有文件
- 支持子目录：可在 `user/Src/` 下自由创建目录层级
- `CONFIGURE_DEPENDS` 使 Ninja 在新增/删除文件后自动重新配置

### VS Code 工作区

`project.code-workspace` 按功能区分文件夹：

| 工作区文件夹 | 路径 | 说明 |
|---|---|---|
| Project Root | `.` | Makefile、CMakeLists.txt、.ioc、.ld |
| User Code | `user/` | 你的应用代码 |
| Core [CubeMX] | `Core/` | CubeMX 生成的入口和外设初始化 |
| Drivers [CubeMX] | `Drivers/` | HAL + CMSIS 驱动 |

`Core/` 和 `Drivers/` 已从 VS Code 搜索中排除（`settings.json` → `search.exclude`），全局搜索只搜 `user/` 中的代码。

---

## 工程目录结构

```
your-project/
├── user/                    # 用户应用代码（与 CubeMX 隔离）
│   ├── Src/                   源文件（.c/.cpp/.s）
│   ├── Inc/                   头文件（.h/.hpp）
│   └── user_sources.cmake     自动收集源文件的 CMake 脚本
├── Core/                    # CubeMX 生成的用户代码
├── Drivers/                 # HAL 库 / CMSIS
├── cmake/                   # CMake 配置片段
├── tools/
│   ├── scripts/             # Python 辅助脚本
│   │   ├── get_toolchain.py   下载 ARM GNU 工具链
│   │   ├── get_openocd.py     下载 xPack OpenOCD
│   │   ├── get_jlink.py       下载 J-Link Software Pack
│   │   ├── gen_openocd_cfg.py 生成 .openocd/target.cfg + 下载 SVD
│   │   ├── setup_pyocd.py     安装 pyOCD target pack
│   │   └── new_project.py     创建新项目
│   ├── toolchain/env.mk     # 工具链路径（自动生成）
│   ├── openocd/env.mk       # OpenOCD 路径（自动生成）
│   └── jlink/env.mk         # J-Link 路径（自动生成）
├── .openocd/
│   └── target.cfg           # OpenOCD 配置（自动生成，勿手动编辑）
├── .vscode/
│   ├── launch.json          # 调试配置
│   ├── tasks.json           # 任务配置
│   └── settings.json        # 编辑器 + 工具路径（由脚本填写）
├── CMakeLists.txt           # CubeMX 生成
├── *.ld                     # 链接脚本（CubeMX 生成）
├── *.ioc                    # CubeMX 工程文件
├── *.svd                    # 外设描述文件（自动下载）
├── Makefile                 # 本模板核心
├── project.code-workspace   # VS Code 多根工作区
└── .clangd                  # clangd 配置
```

---

## Makefile 配置

只需修改 `Makefile` 顶部 **CONFIGURE** 区块：

```makefile
PRESET      ?= Debug          # 构建预设：Debug / Release / RelWithDebInfo / MinSizeRel
PREFIX      ?= arm-none-eabi- # 工具链前缀，通常不需要改
FLASH_TOOL  ?= pyocd          # 默认烧录工具：pyocd / jlink / openocd / cubeprog
SERIAL_PORT ?= COM3           # 串口监视器端口
SERIAL_BAUD ?= 115200         # 串口波特率

# 当自动检测失败时在此手动覆盖：
OPENOCD_CFG  ?= -f interface/cmsis-dap.cfg -f target/stm32f3x.cfg
PYOCD_TARGET ?= stm32f334r8tx
```

其余所有值（项目名、MCU 系列、Flash/RAM 大小、J-Link 设备名等）均从 `CMakeLists.txt`、`*.ld`、`*.ioc` 中**自动检测**，无需手动填写。

---

## 常用命令

### 编译

```bash
make                          # Debug 模式编译（默认）
make PRESET=Release           # Release 模式编译
make rebuild                  # 清理后重新编译
make clean                    # 删除当前预设的构建目录
make distclean                # 删除所有构建输出
```

### 烧录

```bash
make flash                    # 使用默认工具（FLASH_TOOL=pyocd）
make flash FLASH_TOOL=jlink   # J-Link 烧录
make flash-openocd            # OpenOCD 烧录
make flash-cubeprog           # STM32CubeProgrammer 烧录
make flash-pyocd PYOCD_TARGET=stm32f334r8tx  # 指定 pyOCD target
make erase                    # 全片擦除
make reset                    # 复位 MCU
```

### 分析

```bash
make size                     # 显示 Flash / RAM 使用量
make nm                       # 列出前 40 个最大符号
make lss                      # 生成反汇编 listing (.lss)
make symbols                  # 导出完整符号表 (.sym)
```

### 调试（命令行）

```bash
make gdbserver                # 启动 OpenOCD GDB server（终端 A）
make gdb                      # 连接 GDB（终端 B）
make serial SERIAL_PORT=COM3  # 打开串口监视器
```

### 诊断

```bash
make toolchain                # 显示检测到的工程信息和工具链版本
make check-tools              # 验证所有工具链二进制可用
make list-presets             # 列出可用的 CMake 预设
make help                     # 完整帮助
```

### 初始化与新建项目

```bash
make setup                    # 完整初始化（下载工具链、OpenOCD 等，每机一次）
make setup-toolchain          # 仅下载工具链
make setup-openocd            # 仅下载 OpenOCD
make gen-openocd-cfg          # 重新生成 .openocd/target.cfg + SVD（每个新项目运行一次）
make new-project NAME=foo     # 在同级目录创建新项目，共享本模板的工具
```

---

## 调试配置

`launch.json` 中提供两个调试配置，按 `F5` 选择：

### Debug (J-Link)
- 使用 J-Link GDB Server
- 设备名由 `gen_openocd_cfg.py` 从 `*.ioc` 中的 `Mcu.CPN` 自动填写
- J-Link Software Pack 路径由 `get_jlink.py` 写入 `settings.json`
- 支持 Live Watch（轮询变量）

### Debug (OpenOCD / DAPLink)
- 适用于 ST-Link v3 / DAPLink / CMSIS-DAP 调试器
- 启动前自动生成 `.openocd/target.cfg`（并行执行，不增加构建时间）
- WORKAREASIZE 自动适配 MCU 的 SRAM 大小，避免 bus fault
- 支持 Live Watch

### Live Watch 使用方法

在 Cortex-Debug 调试面板中，右键点击变量选择 "Add to Live Watch"，或在 LIVE WATCH 区域手动输入变量名。

**注意**：Live Watch 监视的变量必须有固定地址（全局变量或 `static` 局部变量）。栈上的普通局部变量无法被可靠监视。

```c
static uint32_t counter = 0;  // 正确：static 变量有固定地址

void some_function(void) {
    uint32_t temp = 0;  // 错误：栈变量，地址不固定
}
```

---

## 常见错误及解决方法

### 编译报错：`cmake: command not found` / `ninja: command not found`

CMake 或 Ninja 未加入系统 PATH。

**解决**：确认安装后，将 CMake 的 `bin/` 目录和 Ninja 可执行文件所在目录加入系统环境变量 PATH，重启终端。

---

### 编译报错：`arm-none-eabi-gcc: command not found`

工具链未安装或未加入 PATH。

**解决**：运行 `make setup-toolchain`，脚本会自动下载并通过 `tools/toolchain/env.mk` 配置路径，无需修改 PATH。

---

### 调试报错：`Error: target/stm32f3x.cfg not found`

OpenOCD 未安装或路径未配置。

**解决**：运行 `make setup-openocd`，脚本会下载 xPack OpenOCD 并配置路径。

---

### OpenOCD 调试时 MCU 发生 Bus Fault / Hardfault

OpenOCD 的 WORKAREASIZE 超过了 MCU 的实际 SRAM 大小（常见于 12KB SRAM 的 STM32F334 系列）。

**解决**：运行 `make gen-openocd-cfg`，脚本会根据 `*.ld` 中的 RAM 大小自动计算安全的 WORKAREASIZE 并写入 `.openocd/target.cfg`。

---

### pyOCD 烧录报错：`No target support found for 'stm32xxx'`

pyOCD 缺少对应 MCU 的 pack。

**解决**：
```bash
make setup-pyocd
# 或手动：
pyocd pack update && pyocd pack install stm32f334r8tx
```

如果自动检测的 target 不正确，在 Makefile CONFIGURE 区块中手动指定：
```makefile
PYOCD_TARGET ?= stm32f334r8tx
```

---

### clangd 报错：头文件找不到 / IntelliSense 不工作

`compile_commands.json` 不存在或过时。

**解决**：先执行一次编译（`Ctrl+Shift+B`），Makefile 会在编译成功后自动将 `compile_commands.json` 复制到工程根目录。clangd 在根目录找到该文件后即可正常工作。

---

### 调试时 J-Link 设备名不对（`device: STM32XXXXXX`）

`make setup`（或 `make gen-openocd-cfg`）尚未运行，或 `*.ioc` 中没有 `Mcu.CPN` 字段。

**解决**：
1. 运行 `make gen-openocd-cfg`
2. 若仍不正确，在 `launch.json` 中手动修改 `"device"` 字段为正确的 J-Link 设备名（如 `"STM32F334R8"`）

---

### SVD 文件未下载（寄存器视图为空）

网络不可达或 MCU 不在 `cmsis-svd-data` 仓库中。

**解决**：
- 重新运行 `make gen-openocd-cfg`（需要访问 GitHub）
- 或手动从 [cmsis-svd-data](https://github.com/cmsis-svd/cmsis-svd-data/tree/main/data/STMicro) 下载对应 `*.svd` 文件放到工程根目录，然后在 `launch.json` 中手动设置 `"svdFile"` 路径

---

### `make setup` 下载缓慢或失败

脚本从 GitHub 下载，国内网络可能较慢。

**解决**：配置系统代理后重试，脚本内部使用系统代理。或者：
- 工具链：从 [Arm GNU Toolchain 官网](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads) 手动下载后解压到 `tools/toolchain/`，并手动创建 `tools/toolchain/env.mk`
- OpenOCD：从 [xPack OpenOCD Releases](https://github.com/xpack-dev-tools/openocd-xpack/releases) 手动下载后解压到 `tools/openocd/`

---

### VS Code 中烧录任务无法找到 `make`

Windows 上需要安装 `make`。

**解决**：通过 [winlibs](https://winlibs.com/) 或 [MSYS2](https://www.msys2.org/) 安装 `make`，或使用 [GnuWin32](https://gnuwin32.sourceforge.net/packages/make.htm) 的单独安装包，加入 PATH。

---

## 工具脚本说明

| 脚本 | 功能 | 触发方式 |
|------|------|----------|
| `get_toolchain.py` | 下载 ARM GNU 工具链，写入 `tools/toolchain/env.mk` | `make setup-toolchain` |
| `get_openocd.py` | 下载 xPack OpenOCD，写入 `tools/openocd/env.mk`，更新 `settings.json` | `make setup-openocd` |
| `get_jlink.py` | 下载 J-Link Software Pack，写入 `tools/jlink/env.mk`，更新 `settings.json` | 手动运行 |
| `gen_openocd_cfg.py` | 生成 `.openocd/target.cfg`，下载 SVD，更新 `launch.json` | `make gen-openocd-cfg` / 调试前自动 |
| `setup_pyocd.py` | 安装 pyOCD target pack（按需，跳过已安装的） | `make setup-pyocd` |
| `new_project.py` | 创建新项目，复制模板文件，重定向 env.mk | `make new-project NAME=xxx` |

---

## 版本控制说明

`.gitignore` 已预先配置：

- **不跟踪**（体积大或本地生成）：`build/`、`tools/toolchain/arm-gnu-*/`、`tools/openocd/xpack-*/`、`tools/jlink/JLink_*/`、`*.svd`、`.openocd/target.cfg`
- **跟踪**（需要在团队间共享）：`tools/*/env.mk`（记录使用的工具路径）、`.vscode/settings.json`（clangd 配置 + 工具路径）、`user/`（用户应用代码）

新成员 clone 工程后，只需运行一次 `make setup` 即可完成本地环境配置。
