# Run in PowerShell from an extracted release on Windows.
[CmdletBinding()]
param(
    [string]$HostName = 'steamos@frame.local',
    [switch]$Offline,
    [string]$SourceDir = '',
    [switch]$Help
)
$ErrorActionPreference = 'Stop'
if ($Help) {
    @'
Usage: .\install.ps1 [-HostName USER@HOST] [-Offline] [-SourceDir PATH]

Uploads and installs HEV announcements on your awake Steam Frame using Windows
OpenSSH and tar. The default target is steamos@frame.local. SSH/scp may each ask
for your Frame password; no password is stored. -SourceDir is an audio directory
on the Frame, not on this computer. -Offline disables audio downloading.
Use Windows Terminal or a normal PowerShell window. PowerShell ISE cannot
handle the interactive SSH prompts.
'@
    exit 0
}
if ($Host.Name -eq 'Windows PowerShell ISE Host' -or [Console]::IsInputRedirected) {
    throw 'SSH needs an interactive console. Open Windows Terminal or press Win+R, type powershell, and press Enter. Run this installer there, without piping or redirecting its input. PowerShell ISE does not support SSH keyboard prompts.'
}
if ($HostName -notmatch '^([A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9.-]*$') {
    throw 'Use a hostname or IPv4 address, optionally preceded by USER@ (no SSH options or spaces).'
}
foreach ($command in @('ssh', 'scp', 'tar')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "$command is required. Enable the Windows OpenSSH Client if SSH/scp are missing."
    }
}
$files = @(
    'VERSION', 'README.md', 'LICENSE', 'frame_hev.py', 'assets/README.md', 'assets/manifest.json',
    'config/environment', 'systemd/frame-hev.service', 'tools/install.sh', 'tools/install.py',
    'tools/uninstall.sh', 'tools/control.sh', 'tools/fetch_sounds.py'
)
foreach ($file in $files) {
    $item = Get-Item -LiteralPath (Join-Path $PSScriptRoot $file) -ErrorAction SilentlyContinue
    if (-not $item -or $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Missing release file: $file. Extract the whole download first."
    }
}
$token = [Guid]::NewGuid().ToString('N')
$archive = Join-Path ([IO.Path]::GetTempPath()) "frame-hev-$token.tar"
$remoteArchive = "/tmp/frame-hev-$token.tar"
$remoteWork = "/tmp/frame-hev-$token"
$offlineValue = if ($Offline) { '1' } else { '0' }
$remoteScript = @'
set -euo pipefail
work=$1
archive=$2
offline=$3
audio_source=$4
trap 'rm -f -- "$archive"' EXIT
mkdir -m 700 -- "$work"
trap 'rm -rf -- "$work"; rm -f -- "$archive"' EXIT
tar -xf "$archive" -C "$work"
args=()
[[ "$offline" == 0 ]] || args+=(--offline)
[[ -z "$audio_source" ]] || args+=(--source-dir "$audio_source")
bash "$work/tools/install.sh" "${args[@]}" </dev/null
'@
# Windows here-strings can contain CRLF, which must not reach the Linux shell.
$remoteScript = $remoteScript.Replace("`r", '')
# PowerShell 5.1 can drop embedded double quotes in native command arguments.
# Send the exact Bash argument array as base64 JSON; the Frame's existing Python
# decodes it and launches Bash without any shell interpretation of user options.
$remoteArguments = @('bash', '-c', $remoteScript, '--', $remoteWork, $remoteArchive, $offlineValue, $SourceDir)
$argumentJson = ConvertTo-Json -InputObject $remoteArguments -Compress
$argumentData = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($argumentJson))
$remoteCommand = "python3 -c 'import base64,json,subprocess,sys;sys.exit(subprocess.call(json.loads(base64.b64decode(sys.argv[1]))))' " + $argumentData
$uploadAttempted = $false
$remoteAttempted = $false
try {
    # Do not pipe archive bytes through PowerShell's text pipeline.
    & tar -cf $archive -C $PSScriptRoot @files
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the installation archive.' }
    Write-Host "Installing on $HostName. scp and SSH may ask for your Frame password."
    Write-Host 'On first connection, SSH may ask you to confirm the host fingerprint with yes and Enter.'
    Write-Host 'Password typing is invisible. If the yes/no prompt ignores keys, use a normal PowerShell window or Windows Terminal.'
    $uploadAttempted = $true
    & scp -- $archive "${HostName}:$remoteArchive"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. Check that the Frame is awake and SSH is enabled.' }
    $remoteAttempted = $true
    & ssh -T -- $HostName $remoteCommand
    if ($LASTEXITCODE -ne 0) { throw 'Installation failed. See the Frame installer output above.' }
} finally {
    Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
    # A failed scp can leave a partial file. Remove only this invocation's GUID path.
    # Once SSH starts, the remote shell's EXIT trap owns cleanup.
    if ($uploadAttempted -and -not $remoteAttempted) {
        # Never open another password/host-key prompt while handling a failed
        # upload: it can look like the original prompt is stuck in a loop.
        & ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -- $HostName ('rm -f -- ' + $remoteArchive)
        if ($LASTEXITCODE -ne 0) { Write-Warning "Could not remove temporary upload $remoteArchive on the Frame." }
    }
}
