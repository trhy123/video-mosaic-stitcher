# Video Mosaic Stitcher | 视频画面拼接工具

将多个视频同时排列到同一个画面，按输入列表从左到右、从上到下排列。所有小格尺寸相同，支持自定义行列、宽高比、背景色和编码参数。

Arrange multiple videos in an equal-sized grid with configurable layout, duration and encoding settings.

## 下载与依赖

从 [Releases](https://github.com/trhy123/video-mosaic-stitcher/releases) 下载对应平台的构建：Windows x86 或 x64、Linux x86-64、macOS Apple Silicon 或 Intel。解压后运行 `VideoMosaic`；macOS 运行 `VideoMosaic.app`。macOS 构建未经过 Apple 公证，首次打开可能需要在系统设置中允许。

**必须单独安装 `ffmpeg` 和 `ffprobe`。**程序分别按以下顺序查找每个可执行文件：

1. 程序或源码脚本所在目录；
2. 该目录下的 `bin`；
3. 系统环境变量 `PATH`。

macOS `.app` 也会查找应用包所在目录及其 `bin`。FFmpeg 必须编译包含所选的 `libx264` 或 `libx265` 编码器。输入格式取决于 FFmpeg 支持的格式，输出为无音轨的 MP4。

## 使用

1. 添加或拖入视频，通过上移、下移调整顺序；“清空”只清除列表，不删除源文件。
2. 在“画面布局”选择列数和行数。`*` 表示布局有空格；空格填充所选背景色。可选择最后一个不完整行的对齐方式。
3. 设置小格宽高、宽高比或输出尺寸。修改一个尺寸时，程序根据最近指定的参数更新其他值；行列变化时保留小格尺寸。每格宽高会调整为最接近的偶数。
4. 显示策略可选择先贴边保留完整画面（可能留背景），或后贴边铺满小格（可能裁切）；水平和垂直对齐控制填充或裁切的位置。
5. 时长策略可选择最短视频、最长视频（结束的格子显示背景），或**较短视频循环**（循环短视频至最长视频结束）。输出帧率使用第一个视频的平均帧率。
6. 在“视频编码”选择编码器、preset 和码率控制模式。默认 CRF 为 23；`libx264` 默认 `slower`，`libx265` 默认 `slow`。切换编码器时会恢复对应默认 preset，之后可自行修改。
7. 选择输出文件并开始拼接。默认输出到第一个视频目录，名称为 `stitched-mosaic-列数×行数_时间戳.mp4`。

CRF 模式控制质量，不限定文件大小；“试编码估算大小”使用短片段和较快参数，只能粗略参考。目标大小模式按所选时长计算平均码率并编码两遍，预留封装余量；实际大小可能偏离目标，不会截断或自动重试。每次添加、移除、清空或调整输入顺序时，目标大小会按输入总大小重新计算为整数 MB（1 MB = 1,048,576 字节，有输入时至少为 1 MB）。

## 从源码运行

需要 Python 3.10 或更新版本、Tkinter、`tkinterdnd2`、`ffmpeg` 和 `ffprobe`。安装依赖后运行：

```sh
python -m pip install -r requirements.txt
python video_mosaic.py
```

Windows 也可运行 `start-windows.bat`。有些 Linux 发行版需要单独安装系统的 Tkinter 软件包。可运行 `python video_mosaic.py --help` 查看命令行选项。

## 构建

使用目标操作系统和对应架构的 Python 构建：

```sh
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean --windowed --onedir --name VideoMosaic --additional-hooks-dir hooks video_mosaic.py
```

构建结果位于 `dist/`；macOS 图形应用为 `dist/VideoMosaic.app`。打包程序包含 Python 和 TkDND，不包含 `ffmpeg`、`ffprobe`。运行 `python -m unittest discover -s tests -v` 可执行测试；涉及视频编码的测试需要 FFmpeg。发布 Release 时 GitHub Actions 会构建上述五个平台版本。

## 许可证

本项目使用 [MIT 许可证](LICENSE)。第三方组件适用各自的许可证，详见 `third-party/`。
