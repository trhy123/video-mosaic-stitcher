import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import statistics
import math
import re
from collections import Counter
from datetime import datetime
from fractions import Fraction
from pathlib import Path
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
RATE_MODES = ('CRF（质量控制）', '目标大小（两遍编码）')
FIT_STRATEGY = '先贴边（完整，可能留空/黑边）'
CROP_STRATEGY = '后贴边（铺满，可能裁切）'


def layouts(n):
    return [(r, c) for r in range(1, n + 1) for c in range(1, n + 1)
            if (r - 1) * c < n <= r * c]


def format_size(size):
    value = float(size)
    for unit in ('字节', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{int(value):,} 字节' if unit == '字节' else f'{value:,.2f} {unit}'
        value /= 1024


def format_time(seconds):
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    return f'{hours}:{minutes:02d}:{millis / 1000:06.3f}'


def executable(name):
    app_dir = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
    filename = name + ('.exe' if os.name == 'nt' else '')
    candidates = [app_dir / filename, app_dir / 'bin' / filename]
    # A macOS .app executable lives under Contents/MacOS; also allow tools
    # alongside the application bundle where users can place them normally.
    if getattr(sys, 'frozen', False) and sys.platform == 'darwin' and app_dir.name == 'MacOS':
        bundle_parent = app_dir.parent.parent.parent
        candidates.extend((bundle_parent / filename, bundle_parent / 'bin' / filename))
    for candidate in candidates:
        if candidate.is_file() and (os.name == 'nt' or os.access(candidate, os.X_OK)):
            return str(candidate)
    path = shutil.which(name)
    if not path:
        raise ValueError(f'找不到 {name}。请将 FFmpeg 放到程序目录、程序的 bin 目录，或加入 PATH。')
    return path


def parse_ratio(value):
    try:
        ratio = Fraction(value.strip().replace(':', '/'))
        if ratio <= 0:
            raise ValueError()
        return ratio.limit_denominator(10000)
    except (ValueError, ZeroDivisionError):
        raise ValueError('小格子宽高比无效，请输入例如 16:9 或 4:3。')


def nearest_even(value):
    value = Fraction(value)
    lower = max(2, (value.numerator // (2 * value.denominator)) * 2)
    return lower if value - lower <= lower + 2 - value else lower + 2


def resolve_dimensions(fields, order, rows, cols):
    """Resolve the most recently specified compatible dimensions into an even tile."""
    parsed = {}
    for name in ('width', 'height'):
        text = fields[name].strip()
        if text:
            try:
                number = int(text)
                if number <= 0:
                    raise ValueError()
            except ValueError:
                raise ValueError('输出宽度和高度必须是正整数。')
            count = cols if name == 'width' else rows
            parsed[name] = nearest_even(Fraction(number, count))
    if fields['ratio'].strip():
        parsed['ratio'] = parse_ratio(fields['ratio'])
    if fields['cell_size'].strip():
        match = re.fullmatch(r'\s*(\d+)\s*[xX×*＊]\s*(\d+)\s*', fields['cell_size'])
        if not match or int(match[1]) <= 0 or int(match[2]) <= 0:
            raise ValueError('小格宽高请输入正整数，例如 640×360。')
        parsed['cell_size'] = (nearest_even(int(match[1])), nearest_even(int(match[2])))
    recent = [name for name in reversed(order) if name in parsed]
    if not recent:
        raise ValueError('请填写小格宽高，或填写输出宽度、输出高度和小格宽高比中的任意两项。')
    first = recent[0]
    second = recent[1] if len(recent) > 1 else None
    use_ratio = False
    if first == 'cell_size':
        tile_w, tile_h = parsed[first]
    elif first == 'width':
        tile_w = parsed[first]
        if second == 'height': tile_h = parsed[second]
        elif second == 'ratio': tile_h = nearest_even(tile_w / parsed[second]); use_ratio = True
        elif second == 'cell_size': tile_h = parsed[second][1]
        else: raise ValueError('请再填写输出高度、小格宽高比或小格宽高。')
    elif first == 'height':
        tile_h = parsed[first]
        if second == 'width': tile_w = parsed[second]
        elif second == 'ratio': tile_w = nearest_even(tile_h * parsed[second]); use_ratio = True
        elif second == 'cell_size': tile_w = parsed[second][0]
        else: raise ValueError('请再填写输出宽度、小格宽高比或小格宽高。')
    else:
        ratio = parsed['ratio']; use_ratio = True
        if second == 'width':
            tile_w = parsed[second]; tile_h = nearest_even(tile_w / ratio)
        elif second == 'height':
            tile_h = parsed[second]; tile_w = nearest_even(tile_h * ratio)
        elif second == 'cell_size':
            # The ratio conflicts with the older tile: retain its last-edited axis.
            if order.index('width') > order.index('height'):
                tile_w = parsed[second][0]; tile_h = nearest_even(tile_w / ratio)
            else:
                tile_h = parsed[second][1]; tile_w = nearest_even(tile_h * ratio)
        else:
            raise ValueError('请再填写输出宽度、输出高度或小格宽高。')
    ratio = parsed['ratio'] if use_ratio else Fraction(tile_w, tile_h)
    return {'width': str(tile_w * cols), 'height': str(tile_h * rows),
            'cell_size': f'{tile_w}×{tile_h}',
            'ratio': fields['ratio'].strip() if use_ratio else f'{ratio.numerator}:{ratio.denominator}'}


def probe(path):
    cmd = [executable('ffprobe'), '-v', 'error', '-show_entries',
           'stream=index,codec_type,codec_name,width,height,duration,avg_frame_rate,nb_frames,sample_aspect_ratio,bit_rate:stream_tags=rotate:stream_side_data=rotation:format=duration,bit_rate,format_name',
           '-of', 'json', str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=NO_WINDOW)
    if result.returncode:
        raise ValueError(f'无法读取视频 {path}：{result.stderr}')
    data = json.loads(result.stdout)
    streams = data.get('streams', [])
    s = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if not s:
        raise ValueError(f'没有视频轨道：{path}')
    try:
        duration = float(s.get('duration') or data.get('format', {}).get('duration'))
        fps = Fraction(s['avg_frame_rate'])
        if duration <= 0 or fps <= 0:
            raise ValueError()
        sar = parse_ratio(s.get('sample_aspect_ratio') if s.get('sample_aspect_ratio') not in (None, 'N/A', '0:1') else '1:1')
        w, h = int(s['width']), int(s['height'])
        rotation = int(next((x['rotation'] for x in s.get('side_data_list', []) if 'rotation' in x), s.get('tags', {}).get('rotate', 0))) % 180
        ratio = Fraction(w, h) * sar
        if rotation == 90:
            w, h, ratio = h, w, 1 / ratio
    except (ValueError, KeyError, TypeError, ZeroDivisionError):
        raise ValueError(f'无法确定视频的尺寸、时长或帧率：{path}')
    formats = data.get('format', {}).get('format_name', '').split(',')
    suffix = Path(path).suffix.lower().lstrip('.')
    container = suffix if suffix in formats else (','.join(x for x in formats if x) or '未知')
    return {'width': w, 'height': h, 'ratio': ratio, 'duration': duration, 'fps': fps, 'container': container,
            'bitrate': int(s.get('bit_rate') or data.get('format', {}).get('bit_rate') or 0),
            'codec': s.get('codec_name', '?'), 'audio': any(x.get('codec_type') == 'audio' for x in streams),
            'tracks': len(streams), 'frames': s.get('nb_frames') if str(s.get('nb_frames', '')).isdigit() else None,
            'size': Path(path).stat().st_size}


def default_dimensions(info, ratio, rows, cols):
    height = max(2, info['height'] // 2 * 2)
    width = nearest_even(height * ratio)
    return width * cols, height * rows


def target_bitrate(target_mb, duration):
    if not math.isfinite(target_mb) or target_mb <= 0:
        raise ValueError('目标大小必须是大于零的数字。')
    target_bytes = int(target_mb * 1024 * 1024)
    # Reserve 2% and 16 KiB for the MP4 container, rather than using -fs.
    video_budget = int(target_bytes * 0.98) - 16 * 1024
    kbps = int(video_budget * 8 / duration / 1000)
    if video_budget <= 0 or kbps < 1:
        raise ValueError('目标大小对该时长过小，无法计算有效码率。')
    return target_bytes, kbps


def compose(paths, output, rows, cols, encoder, preset, crf, color, duration_mode,
            width=None, height=None, log=print, ratio=None, strategy='fit',
            align_x='center', align_y='center', profile='', tune='', sample_seconds=None, last_align='left',
            target_mb=None):
    ffmpeg = executable('ffmpeg')
    paths = [Path(p).resolve() for p in paths]
    output = Path(output).resolve()
    if not paths or (rows, cols) not in layouts(len(paths)):
        raise ValueError('视频数量或布局不正确。')
    if output in paths:
        raise ValueError('输出文件不能覆盖输入视频。')
    if output.suffix.lower() != '.mp4' or not output.parent.is_dir():
        raise ValueError('输出文件必须为已存在目录中的 MP4 文件。')
    if encoder not in ('libx264', 'libx265') or (target_mb is None and not 0 <= crf <= 51):
        raise ValueError('编码器或 CRF 无效（CRF 范围为 0–51）。')
    if sample_seconds and target_mb is not None:
        raise ValueError('目标大小模式通过两遍编码控制平均码率，不使用 CRF 试编码估算。')
    if len(color) != 7 or color[0] != '#' or any(c not in '0123456789abcdefABCDEF' for c in color[1:]):
        raise ValueError('背景颜色必须为 #RRGGBB。')
    info = [probe(p) for p in paths]
    ratio = parse_ratio(ratio) if ratio else info[0]['ratio']
    default_w, default_h = default_dimensions(info[0], ratio, rows, cols)
    width = default_w if width is None else width
    height = default_h if height is None else height
    if width < 2 * cols or height < 2 * rows or width % (2 * cols) or height % (2 * rows):
        raise ValueError(f'输出宽度须能被 {2 * cols} 整除，高度须能被 {2 * rows} 整除。')
    tile_w, tile_h = width // cols, height // rows
    if abs(tile_w - tile_h * float(ratio)) > max(2, float(ratio) * 2):
        raise ValueError('输出宽高与设定的小格子比例不一致，请调整输出尺寸或小格子比例。')
    if strategy not in ('fit', 'crop') or align_x not in ('left', 'center', 'right') or align_y not in ('top', 'center', 'bottom'):
        raise ValueError('显示策略或对齐方式无效。')
    if duration_mode not in ('shortest', 'longest', 'loop'):
        raise ValueError('无效的时长策略。')
    duration = (min if duration_mode == 'shortest' else max)(x['duration'] for x in info)
    run_duration = min(duration, sample_seconds) if sample_seconds else duration
    fps = info[0]['fps']
    if target_mb is not None:
        target_bytes, bitrate_kbps = target_bitrate(target_mb, duration)
    log(f'输出 {width}×{height}，{fps} fps，时长 {format_time(duration)}；输入总大小 {format_size(sum(x["size"] for x in info))}。')
    sample_preset, sample_crf = ('veryfast', 18 if crf == 0 else crf) if sample_seconds else (preset, crf)
    if sample_seconds and (sample_preset != preset or sample_crf != crf):
        log('试编码使用较快的替代参数，结果不能可靠预测当前正式编码参数的文件大小。')
    filters = []
    for i in range(len(paths)):
        pre = f'[{i}:v:0]setpts=PTS-STARTPTS,fps={fps.numerator}/{fps.denominator},scale=trunc(iw*sar/2)*2:ih,setsar=1,'
        if strategy == 'fit':
            x = {'left': '0', 'center': '(ow-iw)/2', 'right': 'ow-iw'}[align_x]
            y = {'top': '0', 'center': '(oh-ih)/2', 'bottom': 'oh-ih'}[align_y]
            shape = f'scale={tile_w}:{tile_h}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={tile_w}:{tile_h}:{x}:{y}:color=0x{color[1:]}'
        else:
            x = {'left': '0', 'center': '(iw-ow)/2', 'right': 'iw-ow'}[align_x]
            y = {'top': '0', 'center': '(ih-oh)/2', 'bottom': 'ih-oh'}[align_y]
            shape = f'scale={tile_w}:{tile_h}:force_original_aspect_ratio=increase:force_divisible_by=2,crop={tile_w}:{tile_h}:{x}:{y}'
        filters.append(pre + shape + f',setsar=1,tpad=stop_mode=add:stop_duration={run_duration:.6f}:color=0x{color[1:]},trim=duration={run_duration:.6f}[v{i}]')
    if last_align not in ('left', 'center', 'right'):
        raise ValueError('不完整行的对齐方式无效。')
    remainder = len(paths) % cols
    remaining_width = (cols - remainder) * tile_w if remainder else 0
    offset = {'left': 0, 'center': remaining_width // 2, 'right': remaining_width}[last_align]
    last_occupied_row = (len(paths) - 1) // cols
    positions = '|'.join(f'{(i % cols) * tile_w + (offset if remainder and i // cols == last_occupied_row else 0)}_{(i // cols) * tile_h}' for i in range(len(paths)))
    if len(paths) == 1:
        filters.append('[v0]null[out]')
    else:
        filters.append(''.join(f'[v{i}]' for i in range(len(paths))) + f'xstack=inputs={len(paths)}:layout={positions}:fill=0x{color[1:]},pad={width}:{height}:0:0:color=0x{color[1:]}[out]')
    fd, temp = tempfile.mkstemp(prefix='.mosaic_', suffix='.mp4', dir=str(output.parent))
    os.close(fd)
    try:
        cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y']
        for p, stream_info in zip(paths, info):
            if duration_mode == 'loop' and stream_info['duration'] < duration:
                cmd += ['-stream_loop', '-1']
            cmd += ['-i', str(p)]
        cmd += ['-filter_complex', ';'.join(filters), '-map', '[out]', '-an', '-t', f'{run_duration:.6f}',
                '-c:v', encoder, '-preset', sample_preset, '-pix_fmt', 'yuv420p']
        if profile:
            cmd += ['-profile:v', profile]
        if tune:
            cmd += ['-tune', tune]
        def run_ffmpeg(command, stage):
            result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                    encoding='utf-8', errors='replace', creationflags=NO_WINDOW)
            if result.returncode:
                raise RuntimeError(f'FFmpeg {stage}失败：\n' + result.stderr[-5000:])
        if target_mb is None:
            log(f'{"正在试编码前 %.2f 秒" % run_duration if sample_seconds else "正在编码"}：{encoder} / {sample_preset} / CRF {sample_crf}……')
            run_ffmpeg(cmd + ['-crf', str(sample_crf), '-movflags', '+faststart', temp], '编码')
        else:
            log(f'目标 {format_size(target_bytes)}，预留封装开销后目标平均视频码率 {bitrate_kbps:,} kb/s；实际大小可能偏离目标。')
            with tempfile.TemporaryDirectory(prefix='mosaic-pass-') as stats_dir:
                stats = str(Path(stats_dir) / 'stats')
                common = cmd + ['-b:v', f'{bitrate_kbps}k', '-passlogfile', stats]
                if encoder == 'libx265':
                    common += ['-x265-stats', stats + '.x265']
                log('正在进行第 1/2 遍：分析视频……')
                run_ffmpeg(common + ['-pass', '1', '-f', 'null', os.devnull], '第一遍')
                log('正在进行第 2/2 遍：生成视频……')
                run_ffmpeg(common + ['-pass', '2', '-movflags', '+faststart', temp], '第二遍')
        size = os.path.getsize(temp)
        if sample_seconds:
            estimated = round(size * duration / run_duration)
            log(f'前 {run_duration:.2f} 秒试编码 {format_size(size)}，按时长粗略外推约 {format_size(estimated)}；场景差异及参数差异可能导致较大误差。')
            return estimated
        os.replace(temp, output)
        log(f'完成：{output}（{format_size(size)}）')
        if target_mb is not None:
            diff = size - target_bytes
            log(f'实际大小较目标{"超出" if diff > 0 else "少"} {format_size(abs(diff))}；未截断或重新编码。')
        return size
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class App:
    def __init__(self, root):
        self.root = root
        self.records = []
        self.auto_output = ''
        self.filename_stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        self.revision = 0
        self.updating = False
        self.dim_order = ['cell_size', 'width', 'height', 'ratio']
        self.dim_after = None
        self.last_count = -1
        self.last_layout = None
        self.busy = False
        self.input_signature = ()
        root.title('视频画面拼接')
        root.geometry('1300x520')
        frame = ttk.Frame(root, padding=10); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='拖入视频或点击添加；按表格从上到下顺序，由左到右、从上到下拼接。').pack(anchor='w')
        table_frame = ttk.Frame(frame); table_frame.pack(fill='x', pady=4)
        columns = ('index','path','name','duration','size','ratio','frames','fps','bytes','bitrate','codec','container','audio','tracks')
        self.table = ttk.Treeview(table_frame, columns=columns, show='headings', selectmode='extended', height=6)
        headings = ('序号','路径','文件名','时长','分辨率','长宽比','帧数','帧率','文件大小','码率','编码格式','容器格式','音轨','总轨道数')
        widths = (70,250,300,110,110,100,100,90,100,100,90,100,65,85)
        for key, heading, w in zip(columns, headings, widths):
            self.table.heading(key, text=heading, anchor='center'); self.table.column(key, width=w, minwidth=60, stretch=False, anchor='center' if key in ('size','ratio') else 'e')
        sy = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        sx = ttk.Scrollbar(table_frame, orient='horizontal', command=self.table.xview)
        self.table.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        self.table.grid(row=0, column=0, sticky='nsew'); sy.grid(row=0, column=1, sticky='ns'); sx.grid(row=1, column=0, sticky='ew')
        table_frame.columnconfigure(0, weight=1)
        try:
            from tkinterdnd2 import DND_FILES
            self.table.drop_target_register(DND_FILES)
            self.table.dnd_bind('<<Drop>>', lambda event: self.add_paths(self.root.tk.splitlist(event.data)))
        except Exception as e:
            messagebox.showwarning('拖放不可用', f'拖放扩展加载失败，可使用“添加视频”。\n{e}')
        buttons = ttk.Frame(frame); buttons.pack(fill='x')
        for label, action in [('添加视频', self.add), ('移除', self.remove), ('清空', self.clear), ('上移', lambda: self.move(-1)), ('下移', lambda: self.move(1))]:
            ttk.Button(buttons, text=label, command=action).pack(side='left', padx=(0, 6))
        self.total = tk.StringVar(value='文件数量：0　输入文件总大小：0 字节')
        ttk.Label(buttons, textvariable=self.total).pack(side='left', padx=8)

        self.rows = tk.StringVar(); self.cols = tk.StringVar()
        self.encoder = tk.StringVar(value='libx264'); self.preset = tk.StringVar(value='slower')
        self.profile = tk.StringVar(value='自动'); self.tune = tk.StringVar(value='默认')
        self.crf = tk.StringVar(value='23'); self.width = tk.StringVar(); self.height = tk.StringVar(); self.ratio = tk.StringVar(); self.cell_size = tk.StringVar()
        self.rate_mode = tk.StringVar(value=RATE_MODES[0]); self.target_mb = tk.StringVar()
        self.strategy = tk.StringVar(value=FIT_STRATEGY); self.align_x = tk.StringVar(value='居中'); self.align_y = tk.StringVar(value='居中')
        self.last_align = tk.StringVar(value='左对齐')
        self.color = tk.StringVar(value='#000000'); self.duration = tk.StringVar(value='最短视频'); self.output = tk.StringVar()
        tabs = ttk.Frame(frame); tabs.pack(fill='x', pady=2)
        sidebar = ttk.Frame(tabs); sidebar.pack(side='left', fill='y')
        pages = ttk.Frame(tabs); pages.pack(side='left', fill='both', expand=True)
        pages.columnconfigure(0, weight=1); pages.rowconfigure(0, weight=1)
        self.layout_tab = layout_tab = ttk.Frame(pages, padding=3)
        self.encoding_tab = encoding_tab = ttk.Frame(pages, padding=3)
        for page in (layout_tab, encoding_tab): page.grid(row=0, column=0, sticky='nsew')
        self.page_choice = tk.StringVar(value='画面布局')
        for text, page in (('画面布局', layout_tab), ('视频编码', encoding_tab)):
            ttk.Radiobutton(sidebar, text=text, variable=self.page_choice, value=text, style='Toolbutton',
                            command=page.tkraise).pack(fill='x', pady=(0, 2))
        layout_tab.tkraise()
        def field(parent, index, label, build):
            box = ttk.Frame(parent); box.grid(row=index//4, column=index%4, sticky='ew', padx=4, pady=2)
            ttk.Label(box, text=label).grid(row=0, column=0, sticky='w', padx=(0, 4))
            widget = build(box)
            widget.grid(row=0, column=1, sticky='ew')
            box.columnconfigure(1, weight=1)
            return widget
        def combo(parent, var, values=()):
            return ttk.Combobox(parent, textvariable=var, values=values, state='readonly', width=15)
        self.cols_box = field(layout_tab, 0, '布局：列', lambda p: combo(p, self.cols))
        self.rows_box = field(layout_tab, 1, '布局：行', lambda p: combo(p, self.rows))
        ratio_entry = field(layout_tab, 2, '小格宽高比', lambda p: ttk.Entry(p, textvariable=self.ratio))
        cell_entry = field(layout_tab, 3, '小格宽高', lambda p: ttk.Entry(p, textvariable=self.cell_size))
        width_entry = field(layout_tab, 4, '输出宽度', lambda p: ttk.Entry(p, textvariable=self.width))
        height_entry = field(layout_tab, 5, '输出高度', lambda p: ttk.Entry(p, textvariable=self.height))
        field(layout_tab, 6, '最后不完整行', lambda p: combo(p, self.last_align, ('左对齐','居中','右对齐')))
        field(layout_tab, 7, '时长策略', lambda p: combo(p, self.duration, ('最短视频','最长视频','较短视频循环')))
        field(layout_tab, 8, '显示策略', lambda p: combo(p, self.strategy, (FIT_STRATEGY, CROP_STRATEGY)))
        field(layout_tab, 9, '水平对齐', lambda p: combo(p, self.align_x, ('居中','左','右')))
        field(layout_tab, 10, '垂直对齐', lambda p: combo(p, self.align_y, ('居中','上','下')))
        field(layout_tab, 11, '背景颜色', lambda p: ttk.Button(p, textvariable=self.color, command=self.choose_color))
        field(encoding_tab, 0, 'Encoder', lambda p: combo(p, self.encoder, ('libx264','libx265')))
        field(encoding_tab, 1, 'preset', lambda p: combo(p, self.preset, ('ultrafast','superfast','veryfast','faster','fast','medium','slow','slower','veryslow')))
        self.profile_box = field(encoding_tab, 2, 'profile', lambda p: combo(p, self.profile))
        self.tune_box = field(encoding_tab, 3, 'tune', lambda p: combo(p, self.tune))
        self.rate_box = field(encoding_tab, 4, '文件长度控制模式', lambda p: combo(p, self.rate_mode, RATE_MODES))
        self.crf_entry = field(encoding_tab, 5, 'CRF', lambda p: ttk.Entry(p, textvariable=self.crf))
        self.target_entry = field(encoding_tab, 6, '目标大小 (MB)', lambda p: ttk.Entry(p, textvariable=self.target_mb))
        for tab in (layout_tab, encoding_tab):
            for c in range(4): tab.columnconfigure(c, weight=1, uniform='field')
        self.rows_box.bind('<<ComboboxSelected>>', lambda e: self.change_layout('rows'))
        self.cols_box.bind('<<ComboboxSelected>>', lambda e: self.change_layout('cols'))
        for name, entry in (('width', width_entry), ('height', height_entry), ('ratio', ratio_entry), ('cell_size', cell_entry)):
            getattr(self, name).trace_add('write', lambda *_, n=name: self.dimension_edited(n))
            entry.bind('<FocusOut>', lambda e: self.recalculate_dimensions())
            entry.bind('<Return>', lambda e: self.recalculate_dimensions())
        self.encoder.trace_add('write', lambda *_: self.update_encoder())
        self.update_encoder()
        dest = ttk.Frame(frame); dest.pack(fill='x', pady=4)
        ttk.Label(dest, text='输出文件').pack(side='left')
        ttk.Entry(dest, textvariable=self.output).pack(side='left', fill='x', expand=True, padx=6)
        ttk.Button(dest, text='选择位置', command=self.choose_output).pack(side='left')
        actions = ttk.Frame(frame); actions.pack(fill='x')
        self.start_button = ttk.Button(actions, text='开始拼接', command=lambda: self.start(False)); self.start_button.pack(side='left')
        self.sample_button = ttk.Button(actions, text='试编码估算大小', command=lambda: self.start(True)); self.sample_button.pack(side='left', padx=6)
        self.estimate = tk.StringVar(value='预计大小：尚未试编码'); ttk.Label(actions, textvariable=self.estimate).pack(side='left', padx=6)
        ttk.Label(actions, text='估算仅供参考；输出不含音频。').pack(side='left', padx=6)
        logframe = ttk.Frame(frame); logframe.pack(fill='x')
        self.logbox = tk.Text(logframe, height=3, state='disabled', wrap='none')
        ly = ttk.Scrollbar(logframe, orient='vertical', command=self.logbox.yview); lx = ttk.Scrollbar(logframe, orient='horizontal', command=self.logbox.xview)
        self.logbox.configure(yscrollcommand=ly.set, xscrollcommand=lx.set)
        self.logbox.grid(row=0, column=0, sticky='nsew'); ly.grid(row=0, column=1, sticky='ns'); lx.grid(row=1, column=0, sticky='ew')
        logframe.rowconfigure(0, weight=1); logframe.columnconfigure(0, weight=1)
        for var in (self.rows,self.cols,self.encoder,self.preset,self.profile,self.tune,self.crf,self.rate_mode,self.target_mb,self.width,self.height,self.ratio,self.cell_size,self.strategy,self.align_x,self.align_y,self.last_align,self.duration,self.color):
            var.trace_add('write', lambda *_: self.invalidate_estimate())
        self.rate_mode.trace_add('write', lambda *_: self.update_rate_mode())
        self.update_rate_mode()
        root.update_idletasks()
        root.geometry(f'1300x{frame.winfo_reqheight()}')

    def invalidate_estimate(self):
        self.revision += 1
        self.estimate.set('目标大小为近似值，实际大小可能偏离' if self.is_target_mode() else '预计大小：参数已变化，请重新试编码')

    def update_encoder(self):
        if self.encoder.get() == 'libx264':
            profiles, tunes = ('自动','baseline','main','high','high10','high422','high444'), ('默认','film','animation','grain','stillimage','psnr','ssim','fastdecode','zerolatency')
            self.preset.set('slower')
        else:
            profiles, tunes = ('自动','main','main10'), ('默认','psnr','ssim','grain','fastdecode','zerolatency','animation')
            self.preset.set('slow')
        self.profile_box['values'] = profiles; self.tune_box['values'] = tunes
        self.profile.set('自动'); self.tune.set('默认')

    def is_target_mode(self):
        return self.rate_box.current() == 1

    def update_rate_mode(self):
        is_target = self.is_target_mode()
        self.crf_entry.configure(state='disabled' if is_target else 'normal')
        self.target_entry.configure(state='normal' if is_target else 'disabled')
        if is_target and not self.target_mb.get().strip() and self.records:
            size = sum(record[1]['size'] for record in self.records)
            self.target_mb.set(str(max(1, (size + 524288) // 1048576)))
        self.sample_button.configure(state='disabled' if is_target or self.busy else 'normal')
        if is_target:
            self.estimate.set('目标大小为近似值，实际大小可能偏离')

    def selected_layout(self):
        return int(self.rows.get().rstrip('*')), int(self.cols.get().rstrip('*'))

    def set_layout(self, rows, cols, preserve_tile=True):
        old = self.last_layout
        self.updating = True
        try:
            marker = '*' if rows * cols > len(self.records) else ''
            self.rows.set(f'{rows}{marker}'); self.cols.set(f'{cols}{marker}')
            pairs = layouts(len(self.records))
            self.rows_box['values'] = tuple(str(r) + ('*' if (r, cols) in pairs and r*cols > len(self.records) else '') for r in sorted(set(r for r,c in pairs)))
            self.cols_box['values'] = tuple(str(c) + ('*' if (rows, c) in pairs and rows*c > len(self.records) else '') for c in sorted(set(c for r,c in pairs)))
            if preserve_tile and old and self.width.get().isdigit() and self.height.get().isdigit():
                self.width.set(str(int(self.width.get()) // old[1] * cols))
                self.height.set(str(int(self.height.get()) // old[0] * rows))
            elif self.records:
                try:
                    w, h = default_dimensions(self.records[0][1], parse_ratio(self.ratio.get()), rows, cols)
                    self.width.set(str(w)); self.height.set(str(h))
                except ValueError:
                    pass
            if self.width.get().isdigit() and self.height.get().isdigit():
                self.cell_size.set(f'{int(self.width.get()) // cols}×{int(self.height.get()) // rows}')
            self.last_layout = (rows, cols)
        finally:
            self.updating = False
        self.update_default_output()

    def update_default_output(self):
        if self.records and self.last_layout and (not self.output.get() or self.output.get() == self.auto_output):
            rows, cols = self.last_layout
            name = f'stitched-mosaic-{cols}×{rows}_{self.filename_stamp}.mp4'
            self.auto_output = str(self.records[0][0].with_name(name))
            self.output.set(self.auto_output)

    def change_layout(self, changed):
        pairs = layouts(len(self.records))
        try:
            row, col = self.selected_layout()
            if self.dim_after and self.last_layout:
                self.recalculate_dimensions(layout=self.last_layout)
            if (row, col) not in pairs:
                if changed == 'rows':
                    col = min((c for r,c in pairs if r == row), key=lambda c: abs(c-col))
                else:
                    row = min((r for r,c in pairs if c == col), key=lambda r: abs(r-row))
            self.set_layout(row, col)
        except (ValueError, TypeError):
            pass

    def dimension_edited(self, name):
        if self.updating: return
        if name in self.dim_order: self.dim_order.remove(name)
        self.dim_order.append(name)
        if self.dim_after: self.root.after_cancel(self.dim_after)
        if getattr(self, name).get().strip():
            self.dim_after = self.root.after(700, self.recalculate_dimensions)

    def recalculate_dimensions(self, layout=None, strict=False):
        if self.updating: return
        if self.dim_after:
            self.root.after_cancel(self.dim_after); self.dim_after = None
        try:
            rows, cols = layout or self.selected_layout()
            fields = {key: getattr(self, key).get() for key in ('width', 'height', 'ratio', 'cell_size')}
            resolved = resolve_dimensions(fields, self.dim_order, rows, cols)
            self.updating = True
            try:
                for name, value in resolved.items():
                    if getattr(self, name).get() != value:
                        getattr(self, name).set(value)
            finally: self.updating = False
            return True
        except (ValueError, ZeroDivisionError) as e:
            if strict: raise ValueError(str(e))
            return False

    def add(self):
        self.add_paths(filedialog.askopenfilenames(filetypes=[('视频文件', '*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mts *.m2ts'), ('所有文件', '*.*')]))

    def add_paths(self, paths):
        errors = []
        for path in paths:
            try:
                p = Path(path).resolve()
                if not p.is_file(): raise ValueError('不是文件')
                self.records.append((p, probe(p)))
            except Exception as e: errors.append(f'{path}：{e}')
        self.refresh(recalculate_ratio=True)
        if errors: messagebox.showerror('部分文件无法添加', '\n'.join(errors))

    def refresh(self, recalculate_ratio=False):
        self.table.delete(*self.table.get_children())
        for i, (p, item) in enumerate(self.records, 1):
            ratio = item['ratio']; fps = item['fps']
            frames = f"{int(item['frames']):,}" if item['frames'] else f"约 {round(item['duration'] * float(fps)):,}"
            self.table.insert('', 'end', values=(f'{i}/{len(self.records)}', str(p.parent), p.name, format_time(item['duration']), f"{item['width']}×{item['height']}", f'{ratio.numerator}:{ratio.denominator}',
                              frames, f'{float(fps):.3f}'.rstrip('0').rstrip('.'), format_size(item['size']),
                              f"{item['bitrate']/1000:.0f} kb/s" if item['bitrate'] else '未知', item['codec'], item['container'], '有' if item['audio'] else '无', item['tracks']))
        n = len(self.records)
        total_size = sum(x[1]['size'] for x in self.records)
        signature = tuple((str(path), item['size']) for path, item in self.records)
        if signature != self.input_signature:
            self.input_signature = signature
            self.target_mb.set(str(max(1, (total_size + 524288) // 1048576)) if n else '')
        summary = f'文件数量：{n}　输入文件总大小：{format_size(total_size)}'
        if n:
            times = [x[1]['duration'] for x in self.records]
            summary += f'　时长：最大 {format_time(max(times))}　最小 {format_time(min(times))}　中位 {format_time(statistics.median(times))}　平均 {format_time(statistics.mean(times))}'
        self.total.set(summary)
        if recalculate_ratio and n:
            counts = Counter(x[1]['ratio'] for x in self.records)
            most = max(counts.values())
            ratio = next(x[1]['ratio'] for x in self.records if counts[x[1]['ratio']] == most)
            self.updating = True
            try: self.ratio.set(f'{ratio.numerator}:{ratio.denominator}')
            finally: self.updating = False
        if n != self.last_count:
            self.last_count = n
            if n:
                row, col = min(layouts(n), key=lambda pair: (abs(pair[0]-pair[1]), pair[0]*pair[1]-n, pair[0]))
                self.set_layout(row, col, preserve_tile=False)
            else:
                self.rows.set(''); self.cols.set(''); self.rows_box['values'] = (); self.cols_box['values'] = (); self.last_layout = None
        self.update_default_output()
        if self.is_target_mode() and not self.target_mb.get().strip():
            self.update_rate_mode()
        self.invalidate_estimate()

    def remove(self):
        ids = self.table.get_children(); selected = set(self.table.selection())
        self.records = [record for i, record in enumerate(self.records) if ids[i] not in selected]
        self.refresh(recalculate_ratio=True)

    def clear(self):
        self.records.clear()
        self.refresh(recalculate_ratio=True)

    def move(self, step):
        selected = self.table.selection()
        if len(selected) != 1: return
        i = self.table.index(selected[0]); j = i + step
        if 0 <= j < len(self.records):
            self.records[i], self.records[j] = self.records[j], self.records[i]
            self.refresh(); self.table.selection_set(self.table.get_children()[j])

    def choose_color(self):
        result = colorchooser.askcolor(color=self.color.get(), parent=self.root)[1]
        if result: self.color.set(result)

    def choose_output(self):
        current = Path(self.output.get()) if self.output.get() else Path.cwd() / f'stitched-mosaic_{self.filename_stamp}.mp4'
        result = filedialog.asksaveasfilename(initialdir=str(current.parent), initialfile=current.name, defaultextension='.mp4', filetypes=[('MP4 视频', '*.mp4')])
        if result: self.output.set(result)

    def log(self, msg):
        def write():
            self.logbox.configure(state='normal'); self.logbox.insert('end', msg + '\n'); self.logbox.see('end'); self.logbox.configure(state='disabled')
        self.root.after(0, write)

    def start(self, sample):
        try:
            paths = [str(x[0]) for x in self.records]
            if not paths: raise ValueError('请至少选择一个视频。')
            is_target = self.is_target_mode()
            if is_target and sample: raise ValueError('目标大小模式不需要 CRF 试编码估算。')
            rows, cols = self.selected_layout()
            self.recalculate_dimensions(strict=True)
            try:
                w, h = int(self.width.get()), int(self.height.get())
            except ValueError:
                raise ValueError('输出宽度和输出高度必须是有效整数。')
            output = self.output.get().strip()
            if not output: raise ValueError('请选择输出文件。')
            if Path(output).resolve() in [Path(p).resolve() for p in paths]: raise ValueError('输出文件不能覆盖输入视频。')
            if not sample and Path(output).exists() and not messagebox.askyesno('确认覆盖', '输出文件已存在，成功编码后覆盖它吗？'): return
            if is_target:
                try: target_mb = float(self.target_mb.get())
                except ValueError: raise ValueError('请输入有效的目标大小（MB）。')
                duration = (min if self.duration.get() == '最短视频' else max)(record[1]['duration'] for record in self.records)
                target_bitrate(target_mb, duration)
                crf = 0  # unused in target-size mode
            else:
                try: crf = int(self.crf.get())
                except ValueError: raise ValueError('CRF 必须是 0–51 的整数。')
                if not 0 <= crf <= 51: raise ValueError('CRF 必须是 0–51 的整数。')
                target_mb = None
            args = (paths, output, rows, cols, self.encoder.get(), self.preset.get(), crf, self.color.get(),
                    {'最短视频': 'shortest', '最长视频': 'longest', '较短视频循环': 'loop'}[self.duration.get()], w, h)
            sample_crf = 18 if args[6] == 0 else args[6]
            sample_differs = args[5] != 'veryfast' or sample_crf != args[6]
            options = dict(ratio=self.ratio.get(), strategy={FIT_STRATEGY:'fit', CROP_STRATEGY:'crop'}[self.strategy.get()],
                           align_x={'居中':'center','左':'left','右':'right'}[self.align_x.get()], align_y={'居中':'center','上':'top','下':'bottom'}[self.align_y.get()],
                           last_align={'左对齐':'left','居中':'center','右对齐':'right'}[self.last_align.get()],
                           profile='' if self.profile.get() == '自动' else self.profile.get(), tune='' if self.tune.get() == '默认' else self.tune.get(), sample_seconds=2 if sample else None, target_mb=target_mb)
        except (ValueError, TypeError, KeyError) as e:
            messagebox.showerror('设置错误', str(e)); return
        self.busy = True
        self.start_button.configure(state='disabled'); self.sample_button.configure(state='disabled')
        revision = self.revision
        def run():
            try:
                size = compose(*args, log=self.log, **options)
                if sample:
                    def show_estimate():
                        if revision == self.revision:
                            label = '非正式参数，不能可靠预测' if sample_differs else '前 2 秒外推'
                            self.estimate.set(f'样本外推 {format_size(size)}（veryfast CRF {sample_crf}；{label}）')
                    self.root.after(0, show_estimate)
            except Exception as e: self.log('错误：' + str(e))
            finally:
                def finished():
                    self.busy = False
                    self.start_button.configure(state='normal')
                    self.update_rate_mode()
                self.root.after(0, finished)
        threading.Thread(target=run, daemon=True).start()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='等尺寸视频画面拼接；不含音频。')
    parser.add_argument('--self-test', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--cli', action='store_true')
    parser.add_argument('--rows', type=int); parser.add_argument('--cols', type=int); parser.add_argument('--output')
    parser.add_argument('--encoder', default='libx264', choices=['libx264','libx265']); parser.add_argument('--preset')
    parser.add_argument('--crf', type=int, default=23); parser.add_argument('--color', default='#000000')
    parser.add_argument('--duration', default='shortest', choices=['shortest','longest','loop']); parser.add_argument('--width', type=int); parser.add_argument('--height', type=int)
    parser.add_argument('--ratio'); parser.add_argument('--strategy', default='fit', choices=['fit','crop'])
    parser.add_argument('--align-x', default='center', choices=['left','center','right']); parser.add_argument('--align-y', default='center', choices=['top','center','bottom'])
    parser.add_argument('--last-align', default='left', choices=['left','center','right'])
    parser.add_argument('--profile', default=''); parser.add_argument('--tune', default=''); parser.add_argument('--sample-seconds', type=float)
    parser.add_argument('--target-mb', type=float, help='目标大小（MB，按 1024 进制）；使用两遍码率编码，忽略 CRF')
    parser.add_argument('videos', nargs='*')
    args = parser.parse_args()
    if args.preset is None: args.preset = 'slower' if args.encoder == 'libx264' else 'slow'
    if args.self_test:
        from tkinterdnd2 import TkinterDnD
        def fail_on_warning(title, message):
            raise RuntimeError(f'{title}: {message}')
        messagebox.showwarning = fail_on_warning
        root = TkinterDnD.Tk(); root.withdraw(); App(root); root.update_idletasks(); root.destroy()
    elif args.cli:
        if not args.rows or not args.cols or not args.output: parser.error('--cli 需要 --rows、--cols 和 --output')
        try: compose(args.videos, args.output, args.rows, args.cols, args.encoder, args.preset, args.crf, args.color, args.duration, args.width, args.height,
                     ratio=args.ratio, strategy=args.strategy, align_x=args.align_x, align_y=args.align_y, profile=args.profile, tune=args.tune, sample_seconds=args.sample_seconds, last_align=args.last_align, target_mb=args.target_mb)
        except Exception as e: parser.exit(1, f'错误：{e}\n')
    else:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk(); App(root); root.mainloop()
