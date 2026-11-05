# qqbot-music 一键安装（Windows）。由 安装.bat 调用，也可以重复运行：装过的会跳过，没装完的接着装。
# 所有东西都装在本目录的 env\（Python、依赖、uv）和 third_party\（Seed-VC、YingMusic-SVC、模型），
# 不改系统里已有的 Python / conda / CUDA，也不写注册表、不改 PATH。卸载 = 删掉整个文件夹。
# -InstallDir 路径：直接装到这个位置（不问）；-Yes：所有提问都按「是」
param([switch]$Yes, [switch]$WithYingMusic, [string]$InstallDir)

$ErrorActionPreference = 'Continue'  # 'Stop' 在 PowerShell 5.1 里会把原生程序写 stderr（进度条、警告）当成错误中断
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $PSScriptRoot

# 固定版本：都是作者机器上跑通的组合
$SeedVcCommit = '51383efd921027683c89e5348211d93ff12ac2a8'
$YingCommit = '4974a80c6044c4557059548409379f6365129f88'
$TorchVersion = '2.6.0'
$PyIndexes = @('https://pypi.tuna.tsinghua.edu.cn/simple', 'https://mirrors.aliyun.com/pypi/simple', 'https://pypi.org/simple')
$TorchMirror = 'https://mirrors.aliyun.com/pytorch-wheels'
$TorchOfficial = 'https://download.pytorch.org/whl'
$PythonMirror = 'https://registry.npmmirror.com/-/binary/python-build-standalone'

function Say($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }
function Step($n, $title) { Write-Host ''; Write-Host "==== [$n/8] $title ====" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }
function Fail($msg) {
    Write-Host ''
    Write-Host "安装没完成：$msg" -ForegroundColor Red
    Write-Host '问题解决后重新双击 安装.bat，装好的部分会跳过。安装记录在 env\install_log.txt，提 issue 时请附上。' -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}
function Ask($q) {
    if ($Yes) { return $true }
    $a = Read-Host "  $q（y/n）"
    return $a -match '^(y|yes|是|好)$'
}
function Get-File($urls, $out) {
    foreach ($u in $urls) {
        for ($i = 1; $i -le 3; $i++) {
            try {
                & curl.exe -L --fail --retry 2 --connect-timeout 20 -C - -o $out $u
                if ($LASTEXITCODE -eq 0 -and (Test-Path $out)) { return $true }
            } catch {}
            Say "  下载失败（$(([uri]$u).Host)，第 $i 次），重试…" 'DarkYellow'
            Start-Sleep 2
        }
        Remove-Item $out -ErrorAction SilentlyContinue
    }
    return $false
}
function Uv-Pip($pipArgs) {
    foreach ($idx in $PyIndexes) {
        & $script:Uv pip install --python $Py --index-url $idx @pipArgs
        if ($LASTEXITCODE -eq 0) { return $true }
        Warn "从 $(([uri]$idx).Host) 装失败，换下一个源"
    }
    return $false
}

Say ''
Say '  qqbot-music 一键安装' 'Magenta'
Say '  所有东西都装在一个文件夹里，不会改动你电脑上已有的 Python / conda / CUDA。'

# ─────────────────────────────────────────────
# 装在哪：默认就是解压出来的这个文件夹；可以换位置（把程序复制过去，在新位置接着装）
function Test-BadPath($p) { return ($p -match '[^\x00-\x7F]') -or ($p -match '\s') }
function Get-FreeGB($p) { return [math]::Round((Get-PSDrive (Split-Path -Qualifier $p).TrimEnd(':')).Free / 1GB, 1) }
$installed = Test-Path (Join-Path $Root 'env\venv\Scripts\python.exe')
$target = $null
if ($InstallDir) {
    $target = $InstallDir
} elseif (-not $installed -and -not $Yes) {
    Say ''
    Say "  当前位置：$Root（所在盘剩余 $(Get-FreeGB $Root) GB，装好约 11 GB，安装过程中下载缓存最多再占约 5 GB）"
    if (Test-BadPath $Root) { Warn '这个路径有中文或空格，部分依赖可能出错，建议换到 D:\qqbot-music 这样的纯英文路径' }
    $c = Read-Host '  装在这里直接回车；想换个位置输入 2 回车（会弹出选文件夹的窗口）'
    if ($c.Trim() -eq '2') {
        Add-Type -AssemblyName System.Windows.Forms
        $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
        $dlg.Description = '选一个文件夹，qqbot-music 会装在它下面的 qqbot-music 文件夹里（建议纯英文路径、剩余空间 18 GB 以上）'
        $dlg.ShowNewFolderButton = $true
        if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            $target = Join-Path $dlg.SelectedPath 'qqbot-music'
        } else { Say '  没选，就装在当前位置' }
    }
}
if ($target) {
    $target = [IO.Path]::GetFullPath($target)
    if ($target.TrimEnd('\') -ne $Root.TrimEnd('\')) {
        if (Test-BadPath $target) { Warn "新位置也有中文或空格：$target" }
        Say "  把程序复制到 $target …"
        New-Item -ItemType Directory -Force $target | Out-Null
        & robocopy $Root $target /E /XD .git env third_party __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8 -or -not (Test-Path (Join-Path $target 'install\install.ps1'))) { Fail "复制到 $target 失败" }
        Say "  复制好了。以后用 $target 里的文件，原来解压的文件夹装完可以删掉。" 'Green'
        $next = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $target 'install\install.ps1'), '-InstallDir', $target)
        if ($Yes) { $next += '-Yes' }
        if ($WithYingMusic) { $next += '-WithYingMusic' }
        & "$PSHOME\powershell.exe" @next
        exit $LASTEXITCODE
    }
}

$EnvDir = Join-Path $Root 'env'
$Venv = Join-Path $EnvDir 'venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
$ThirdParty = Join-Path $Root 'third_party'
New-Item -ItemType Directory -Force $EnvDir, $ThirdParty | Out-Null
Start-Transcript -Path (Join-Path $EnvDir 'install_log.txt') -Append | Out-Null
Say "  安装位置：$Root"

# ─────────────────────────────────────────────
Step 1 '检查电脑环境'
if (-not [Environment]::Is64BitOperatingSystem) { Fail '需要 64 位 Windows' }
$os = [Environment]::OSVersion.Version
Say "  系统：Windows $($os.Major).$($os.Minor) build $($os.Build)"
if ($os.Major -lt 10) { Fail '需要 Windows 10 或 11' }

if (Test-BadPath $Root) {
    Warn "安装路径里有中文或空格：$Root"
    Warn '部分依赖在这种路径下可能出错。不想冒险的话选 n，重新双击 安装.bat 时输入 2 换位置。'
    if (-not (Ask '仍然在这里装？')) { Fail '已取消，换个位置再装' }
}

$drive = (Split-Path -Qualifier $Root).TrimEnd(':')
$freeGB = Get-FreeGB $Root
Say "  $($drive) 盘剩余：$freeGB GB（完整安装约需 15 GB，之后每首歌再占几十 MB）"
if ($freeGB -lt 18 -and -not (Test-Path $Py)) {
    Warn '磁盘空间可能不够'
    if (-not (Ask '继续？')) { Fail '磁盘空间不够' }
}

# 显卡
$torchTag = 'cpu'
$smi = (Get-Command nvidia-smi -ErrorAction SilentlyContinue).Source
if (-not $smi -and (Test-Path "$env:SystemRoot\System32\nvidia-smi.exe")) { $smi = "$env:SystemRoot\System32\nvidia-smi.exe" }
if ($smi) {
    $gpu = (& $smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
}
if ($gpu) {
    $name, $mem, $drv = $gpu.Split(',') | ForEach-Object { $_.Trim() }
    Say "  显卡：$name，显存 $mem MB，驱动 $drv"
    $drvVer = [version]($drv + '.0')
    if ($drvVer -ge [version]'528.33') { $torchTag = 'cu124' }
    elseif ($drvVer -ge [version]'452.39') { $torchTag = 'cu118'; Warn '显卡驱动比较旧，用 CUDA 11.8 版本；更新驱动后速度可能更好' }
    else {
        Warn '显卡驱动太旧（低于 452），用不了 GPU。请到 NVIDIA 官网或 NVIDIA App 更新驱动后重新安装。'
        if (-not (Ask '先装 CPU 版（非常慢，学一首歌要几十分钟）？')) { Fail '请先更新显卡驱动' }
    }
    if ([int]$mem -lt 3800) {
        Warn "显存只有 $mem MB，换声和分离至少要 4G 左右，可能会显存不够。"
        if (-not (Ask '继续？')) { Fail '显存不够' }
    }
} else {
    Warn '没检测到 NVIDIA 显卡（AMD / Intel 显卡、核显都用不了 GPU 加速）。'
    Warn 'CPU 也能跑但非常慢：分离 30 秒音频要好几分钟，学一首歌要几十分钟。'
    if (-not (Ask '仍然安装 CPU 版？')) { Fail '没有 NVIDIA 显卡' }
}
Say "  PyTorch 版本：$TorchVersion+$torchTag"

# 已有的环境：只报告，不动
$found = @()
foreach ($t in 'python', 'conda', 'uv', 'git') {
    $c = Get-Command $t -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { $found += "$t（$($c.Source)）" }
}
if ($found) { Say "  电脑上已有：$($found -join '，')。都不会用到也不会改动。" }
if ($env:HTTPS_PROXY -or $env:HTTP_PROXY) { Say "  检测到代理环境变量，下载会走代理：$($env:HTTPS_PROXY)$($env:HTTP_PROXY)" }
if (Test-Path $Py) { Say '  检测到上次的安装，接着装 / 修复。' 'Green' }

# ─────────────────────────────────────────────
Step 2 '准备安装工具 uv'
$ToolDir = Join-Path $EnvDir 'tools'
$script:Uv = Join-Path $ToolDir 'uv.exe'
if (Test-Path $script:Uv) {
    Say '  已有，跳过'
} else {
    New-Item -ItemType Directory -Force $ToolDir | Out-Null
    $tmp = Join-Path $EnvDir 'uv_download.zip'
    $ok = $false
    try {  # 清华 PyPI 镜像上的 uv wheel（就是个 zip，里面有 uv.exe）
        $html = (Invoke-WebRequest -UseBasicParsing 'https://pypi.tuna.tsinghua.edu.cn/simple/uv/').Content
        $wheels = [regex]::Matches($html, 'href="([^"]*?/(uv-(\d+)\.(\d+)\.(\d+)-py3-none-win_amd64\.whl))[^"]*"')
        $best = $wheels | Sort-Object { [int]$_.Groups[3].Value }, { [int]$_.Groups[4].Value }, { [int]$_.Groups[5].Value } | Select-Object -Last 1
        $url = [uri]::new([uri]'https://pypi.tuna.tsinghua.edu.cn/simple/uv/', $best.Groups[1].Value).AbsoluteUri
        Say "  下载 $($best.Groups[2].Value)"
        $ok = Get-File @($url) $tmp
        if ($ok) {
            $x = Join-Path $EnvDir 'uv_extract'
            Expand-Archive $tmp $x -Force
            Copy-Item (Get-ChildItem $x -Recurse -Filter uv.exe | Select-Object -First 1).FullName $script:Uv
            Remove-Item $x -Recurse -Force
        }
    } catch { $ok = $false }
    if (-not (Test-Path $script:Uv)) {
        Say '  镜像不行，改从 GitHub 下'
        if (Get-File @('https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip') $tmp) {
            $x = Join-Path $EnvDir 'uv_extract'
            Expand-Archive $tmp $x -Force
            Copy-Item (Get-ChildItem $x -Recurse -Filter uv.exe | Select-Object -First 1).FullName $script:Uv
            Remove-Item $x -Recurse -Force
        }
    }
    Remove-Item $tmp -ErrorAction SilentlyContinue
    if (-not (Test-Path $script:Uv)) { Fail '下载不了 uv（安装工具），检查网络后重试' }
    Say '  好了' 'Green'
}
$env:UV_CACHE_DIR = Join-Path $EnvDir 'cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $EnvDir 'python'
$env:UV_LINK_MODE = 'copy'
$env:UV_HTTP_TIMEOUT = '120'

# ─────────────────────────────────────────────
Step 3 '安装独立的 Python 3.10（放在 env\python，不影响系统）'
$pyOk = $false
if (Test-Path $Py) { $pyOk = (& $Py -c "import sys; print(sys.version_info[:2] == (3, 10))" 2>$null) -eq 'True' }
if ($pyOk) { Say '  已有，跳过' } else {
    if (Test-Path $Venv) { Remove-Item $Venv -Recurse -Force }
    $env:UV_PYTHON_INSTALL_MIRROR = $PythonMirror
    & $script:Uv venv $Venv --python 3.10 --python-preference only-managed
    if ($LASTEXITCODE -ne 0) {
        Warn '国内镜像失败，改从 GitHub 下 Python'
        Remove-Item Env:\UV_PYTHON_INSTALL_MIRROR
        & $script:Uv venv $Venv --python 3.10 --python-preference only-managed
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Py)) { Fail 'Python 安装失败' }
    Say '  好了' 'Green'
}

# ─────────────────────────────────────────────
Step 4 "安装 PyTorch $TorchVersion（$torchTag，约 2.5 GB，最久的一步）"
$have = & $Py -c "import torch; print(torch.__version__)" 2>$null
if ($have -eq "$TorchVersion+$torchTag") { Say "  已有 $have，跳过" } else {
    if ($have) { Say "  现在是 $have，换成 $TorchVersion+$torchTag" }
    $wheels = @("torch-$TorchVersion%2B$torchTag-cp310-cp310-win_amd64.whl", "torchaudio-$TorchVersion%2B$torchTag-cp310-cp310-win_amd64.whl") |
        ForEach-Object { "$TorchMirror/$torchTag/$_" }
    $ok = Uv-Pip (@('--reinstall-package', 'torch', '--reinstall-package', 'torchaudio') + $wheels)
    if (-not $ok) {
        Warn '阿里云镜像失败，改从 PyTorch 官网下（可能比较慢）'
        & $script:Uv pip install --python $Py "torch==$TorchVersion" "torchaudio==$TorchVersion" --index-url "$TorchOfficial/$torchTag" --extra-index-url $PyIndexes[0] --index-strategy unsafe-best-match
        $ok = $LASTEXITCODE -eq 0
    }
    if (-not $ok) { Fail 'PyTorch 安装失败' }
    Say '  好了' 'Green'
}
$cudaOk = & $Py -c "import torch; print(torch.cuda.is_available())" 2>$null
if ($torchTag -ne 'cpu' -and $cudaOk -ne 'True') { Warn 'PyTorch 装好了但用不了显卡，先按 CPU 继续；更新显卡驱动后重新安装试试' }

# ─────────────────────────────────────────────
Step 5 '下载 Seed-VC、YingMusic-SVC 代码（固定版本）'
function Get-Repo($label, $owner, $repo, $commit, $dest, $mustHave) {
    $marker = Join-Path $dest '.qqbot_music_commit'
    if ((Test-Path $marker) -and ((Get-Content $marker -Raw).Trim() -eq $commit)) { Say "  $label 已有，跳过"; return }
    if ((Test-Path (Join-Path $dest $mustHave)) -and -not (Test-Path $marker)) { Say "  $label 检测到已有的代码，直接用"; return }
    $zip = Join-Path $EnvDir "$repo.zip"
    Say "  下载 $label…"
    if (-not (Get-File @("https://codeload.github.com/$owner/$repo/zip/$commit") $zip)) {
        $git = Get-Command git -ErrorAction SilentlyContinue
        if ($git) {
            Say '  改用 git 下载'
            if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
            & git clone "https://github.com/$owner/$repo" $dest
            if ($LASTEXITCODE -eq 0) { & git -C $dest checkout $commit; Set-Content $marker $commit; return }
        }
        Fail "下载不了 $label（GitHub 连不上）。开代理后重试，或者手动下载 https://github.com/$owner/$repo/archive/$commit.zip 解压到 $dest"
    }
    $x = Join-Path $EnvDir "$repo-extract"
    if (Test-Path $x) { Remove-Item $x -Recurse -Force }
    Expand-Archive $zip $x -Force
    $inner = Get-ChildItem $x -Directory | Select-Object -First 1
    if (Test-Path $dest) {  # 保留已经下好的模型（checkpoints / ckpt），只换代码
        Copy-Item (Join-Path $inner.FullName '*') $dest -Recurse -Force
    } else {
        Move-Item $inner.FullName $dest
    }
    Remove-Item $x -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $zip -ErrorAction SilentlyContinue
    Set-Content $marker $commit
    Say "  $label 好了" 'Green'
}
Get-Repo 'Seed-VC' 'Plachtaa' 'seed-vc' $SeedVcCommit (Join-Path $ThirdParty 'seed-vc') 'inference.py'
Get-Repo 'YingMusic-SVC' 'GiantAILab' 'YingMusic-SVC' $YingCommit (Join-Path $ThirdParty 'YingMusic-SVC') 'accom_separation'

# ─────────────────────────────────────────────
Step 6 '安装其余依赖（固定版本）'
# 管理页 / 翻唱服务开着会锁住 .pyd，升级依赖时删不掉、新旧文件混在一起（tokenizers 就出过事），先关掉
$running = Get-Process python, pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith((Join-Path $Root 'env'), [StringComparison]::OrdinalIgnoreCase) }
if ($running) { Say '  先关掉正在运行的管理页 / 翻唱服务'; $running | Stop-Process -Force; Start-Sleep -Seconds 2 }
if (-not (Uv-Pip @('--no-deps', '-r', (Join-Path $PSScriptRoot 'requirements-lock.txt')))) { Fail '依赖安装失败' }
Say '  好了' 'Green'

# ─────────────────────────────────────────────
Step 7 '下载模型并自检（约 6.2 GB，ModelScope）'
$env:PYTHONUTF8 = '1'
$smArgs = @((Join-Path $PSScriptRoot 'setup_models.py'))
if ($WithYingMusic) { $smArgs += '--with-yingmusic' }
& $Py @smArgs
if ($LASTEXITCODE -ne 0) { Fail '模型下载或自检没通过（看上面的报错）' }

# ─────────────────────────────────────────────
Step 8 '收尾'
$cfg = Join-Path $Root 'config.toml'
if (-not (Test-Path $cfg)) { Copy-Item (Join-Path $Root 'config.example.toml') $cfg; Say '  生成了 config.toml（默认设置，一般不用改）' }
New-Item -ItemType Directory -Force (Join-Path $Root 'voice') | Out-Null
Remove-Item (Join-Path $EnvDir 'cache') -Recurse -Force -ErrorAction SilentlyContinue  # 下载缓存，装好就没用了

$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'qqbot-music 管理页.lnk'
if (-not (Test-Path $lnk) -and (Ask '在桌面放一个「qqbot-music 管理页」快捷方式？')) {
    $sh = New-Object -ComObject WScript.Shell
    $s = $sh.CreateShortcut($lnk)
    $s.TargetPath = Join-Path $Root '启动管理页.bat'
    $s.WorkingDirectory = $Root
    $s.Save()
    Say '  放好了'
}

Say ''
Say '==== 装好了 ====' 'Green'
Say '接下来会打开管理页（浏览器），初始密码 123456。按页面提示：'
Say '  1. 改密码   2. 上传一段 10–25 秒的干净录音当音色   3. 填 B 站 cookie   4. 学一首歌、试唱'
Say '以后双击 启动管理页.bat（或桌面快捷方式）就能打开。'
Stop-Transcript | Out-Null
Start-Process -FilePath (Join-Path $Root '启动管理页.bat') -WorkingDirectory $Root
