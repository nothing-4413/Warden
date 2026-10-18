<#
.SYNOPSIS
    用仓库自带的 llama.cpp + GGUF 起本地 LLM（对话 + 嵌入两个实例），不依赖 Ollama。

.DESCRIPTION
    为什么需要两个实例：llama-server 一个进程只加载一个模型。Warden 的 LLMClient 走
    {llm_base_url}/chat/completions，EmbeddingClient 走 {embedding_base_url}/embeddings，
    所以对话挂 :11500、嵌入挂 :11501，配 .env：
        WARDEN_LLM_BASE_URL=http://127.0.0.1:11500/v1
        WARDEN_LLM_MODEL=qwen2.5-1.5b-instruct
        WARDEN_EMBEDDING_BASE_URL=http://127.0.0.1:11501/v1
        WARDEN_EMBEDDING_MODEL=nomic-embed-text-v1.5

    默认模型是仓库 data/eval/models/ 里已有的两个 GGUF（CPU 可用，1.5B Q4_K_M 约 1 GB）。
    PID 落在 data/local-llm-pids.json，`-Stop` 据此收工。

.EXAMPLE
    pwsh -File scripts/local_llm.ps1            # 后台起两个实例
    pwsh -File scripts/local_llm.ps1 -Wait      # 前台等就绪后打印地址（Ctrl+C 结束）
    pwsh -File scripts/local_llm.ps1 -Stop      # 停掉
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$Wait,
    [string]$ChatModel = "qwen2.5-1.5b-instruct-q4_k_m.gguf",
    [string]$EmbedModel = "nomic-embed-text-v1.5.Q8_0.gguf",
    [int]$ChatPort = 11500,
    [int]$EmbedPort = 11501,
    [int]$Threads = 8
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bin = Join-Path $root "data/eval/llama"
$exe = Join-Path $bin "llama-server.exe"
$modelDir = Join-Path $root "data/eval/models"
$logDir = Join-Path $root "logs"
$pidFile = Join-Path $root "data/local-llm-pids.json"

function Test-Port([int]$port) {
    # 不用 Test-NetConnection：它内部走 Write-Progress，在没有可读控制台的宿主
    # （CI / 远程会话 / 非交互 shell）里会抛 "Access is denied reading the console output buffer"。
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect("127.0.0.1", $port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

if ($Stop) {
    if (-not (Test-Path $pidFile)) { "没有 $pidFile，可能本来就没起。"; return }
    foreach ($p in (Get-Content $pidFile -Raw | ConvertFrom-Json)) {
        Stop-Process -Id $p.pid -Force -ErrorAction SilentlyContinue
        "stopped pid=$($p.pid) ($($p.name))"
    }
    Remove-Item $pidFile -Force
    return
}

if (-not (Test-Path $exe)) { throw "找不到 llama-server：$exe" }
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$specs = @(
    @{ name = "chat"; port = $ChatPort; model = $ChatModel; alias = "qwen2.5-1.5b-instruct"; extra = @("-c", "4096", "-t", "$Threads") },
    @{ name = "embed"; port = $EmbedPort; model = $EmbedModel; alias = "nomic-embed-text-v1.5"; extra = @("--embeddings", "-c", "2048", "-t", "4") }
)

$started = @()
foreach ($s in $specs) {
    if (Test-Port $s.port) { "端口 $($s.port) 已被占用，跳过 $($s.name)（可能已经在跑）"; continue }
    $modelPath = Join-Path $modelDir $s.model
    if (-not (Test-Path $modelPath)) { throw "找不到模型：$modelPath" }
    $args = @("-m", $modelPath, "--host", "127.0.0.1", "--port", "$($s.port)",
        "--alias", $s.alias) + $s.extra
    $proc = Start-Process -FilePath $exe -ArgumentList $args -WorkingDirectory $bin -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "llama-$($s.name).log") `
        -RedirectStandardError (Join-Path $logDir "llama-$($s.name).err.log")
    "started $($s.name) pid=$($proc.Id) port=$($s.port) model=$($s.model)"
    $started += [pscustomobject]@{ name = $s.name; pid = $proc.Id; port = $s.port }
}
if ($started.Count -gt 0) { $started | ConvertTo-Json | Set-Content -Path $pidFile -Encoding UTF8 }

# 只等端口能连上（模型加载完才 listen），不追求 /v1/models 返回 200
$deadline = (Get-Date).AddSeconds(60)
foreach ($s in $started) {
    while (-not (Test-Port $s.port)) {
        if ((Get-Date) -gt $deadline) { throw "$($s.name) 60s 内没起来，看 $logDir\llama-$($s.name).err.log" }
        Start-Sleep -Milliseconds 500
    }
    "ready: $($s.name) -> http://127.0.0.1:$($s.port)/v1"
}
"日志目录：$logDir ；停止：pwsh -File scripts/local_llm.ps1 -Stop"

if ($Wait -and $started.Count -gt 0) {
    # 前台守着子进程：脚本不退出，llama-server 就不会被父进程的清理带走
    $started | ForEach-Object { Wait-Process -Id $_.pid -ErrorAction SilentlyContinue }
}
