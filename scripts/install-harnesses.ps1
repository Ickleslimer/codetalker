#Requires -Version 5.1
<#
.SYNOPSIS
  Regenerate CodeTalker MCP entries across local harness configs from one project root.

.DESCRIPTION
  Merges a canonical codetalker MCP server block into Cursor, Codex, Antigravity,
  and Claude Desktop config files. Freebuff cannot be patched reliably from the CLI;
  the script prints manual re-add instructions instead.

.PARAMETER ProjectRoot
  Absolute path to the codetalker repo (default: parent of this scripts/ folder).

.PARAMETER UvCommand
  Path or name of the uv executable (default: resolve via PATH, then ~/.local/bin/uv.exe).

.PARAMETER UseUvTool
  Run `uv tool install` and configure harnesses to invoke `codetalker` on PATH instead of
  `uv run --project <root> codetalker`.

.PARAMETER Harness
  Which harness configs to update: All, Cursor, Codex, Antigravity, Claude (default: All).

.PARAMETER WhatIf
  Show planned changes without writing files.

.EXAMPLE
  .\scripts\install-harnesses.ps1 -ProjectRoot D:\codetalker

.EXAMPLE
  .\scripts\install-harnesses.ps1 -UseUvTool
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string] $ProjectRoot = "",
    [string] $UvCommand = "",
    [switch] $UseUvTool,
    [ValidateSet("All", "Cursor", "Codex", "Antigravity", "Claude")]
    [string[]] $Harness = @("All")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-ProjectRoot {
    param([string] $Root)
    if (-not $Root) {
        $Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    } elseif (-not (Test-Path -LiteralPath $Root)) {
        throw "ProjectRoot not found: $Root"
    } else {
        $Root = (Resolve-Path -LiteralPath $Root).Path
    }
    return ($Root -replace "\\", "/")
}

function Resolve-UvCommand {
    param([string] $Override)
    if ($Override) {
        if (-not (Test-Path -LiteralPath $Override) -and -not (Get-Command $Override -ErrorAction SilentlyContinue)) {
            throw "UvCommand not found: $Override"
        }
        return $Override
    }
    $fromPath = Get-Command uv -ErrorAction SilentlyContinue
    if ($fromPath) {
        return $fromPath.Source
    }
    $candidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\uv\uv.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return "uv"
}

function New-CodetalkerEntry {
    param(
        [string] $ProjectRoot,
        [string] $UvExe,
        [bool] $UseTool,
        [bool] $IncludeType
    )
    if ($UseTool) {
        if ($IncludeType) {
            return [PSCustomObject]@{
                type    = "stdio"
                command = "codetalker"
                args    = @()
            }
        }
        return [PSCustomObject]@{
            command = "codetalker"
            args    = @()
        }
    }
    if ($IncludeType) {
        return [PSCustomObject]@{
            type    = "stdio"
            command = $UvExe
            args    = @("run", "--project", $ProjectRoot, "codetalker")
        }
    }
    return [PSCustomObject]@{
        command = $UvExe
        args    = @("run", "--project", $ProjectRoot, "codetalker")
    }
}

function Backup-File {
    param([string] $Path)
    if (Test-Path -LiteralPath $Path) {
        $backup = "$Path.bak"
        Copy-Item -LiteralPath $Path -Destination $backup -Force
        Write-Host "  backup: $backup"
    }
}

function Merge-JsonMcpConfig {
    param(
        [string] $Path,
        [PSCustomObject] $CodetalkerEntry,
        [string] $Label
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Warning "[$Label] Config not found (skipped): $Path"
        return $false
    }

    $config = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $config.PSObject.Properties["mcpServers"]) {
        $config | Add-Member -NotePropertyName mcpServers -NotePropertyValue (New-Object PSObject)
    }
    $config.mcpServers | Add-Member -NotePropertyName codetalker -NotePropertyValue $CodetalkerEntry -Force

    $json = $config | ConvertTo-Json -Depth 8
    if ($PSCmdlet.ShouldProcess($Path, "Update codetalker MCP entry ($Label)")) {
        Backup-File -Path $Path
        Set-Content -LiteralPath $Path -Value $json -Encoding UTF8
        Write-Host "[$Label] Updated $Path"
    } else {
        Write-Host "[$Label] Would update $Path"
    }
    return $true
}

function Update-CodexToml {
    param(
        [string] $Path,
        [string] $ProjectRoot,
        [string] $UvExe,
        [bool] $UseTool
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Warning "[Codex] Config not found (skipped): $Path"
        return $false
    }

    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ($UseTool) {
        $block = "[mcp_servers.codetalker]`r`ncommand = `"codetalker`"`r`nargs = []`r`n"
    } else {
        $uvEscaped = $UvExe.Replace("\", "\\")
        $block = "[mcp_servers.codetalker]`r`ncommand = `"$uvEscaped`"`r`nargs = [""run"", ""--project"", ""$ProjectRoot"", ""codetalker""]`r`n"
    }

    $pattern = '(?ms)^\[mcp_servers\.codetalker\].*?(?=^\[|\z)'
    if ($raw -match '(?m)^\[mcp_servers\.codetalker\]') {
        $updated = [regex]::Replace($raw, $pattern, $block.TrimEnd() + "`r`n`r`n")
    } else {
        $updated = $raw.TrimEnd() + "`r`n`r`n" + $block
    }

    if ($PSCmdlet.ShouldProcess($Path, "Update [mcp_servers.codetalker]")) {
        Backup-File -Path $Path
        [System.IO.File]::WriteAllText($Path, $updated, [System.Text.UTF8Encoding]::new($false))
        Write-Host "[Codex] Updated $Path"
    } else {
        Write-Host "[Codex] Would update $Path"
    }
    return $true
}

function Install-UvTool {
    param([string] $ProjectRootNative)
    Write-Host "Installing codetalker via uv tool from $ProjectRootNative ..."
    if ($PSCmdlet.ShouldProcess("uv tool", "Install codetalker from $ProjectRootNative")) {
        & uv tool install --force --from $ProjectRootNative codetalker
        if ($LASTEXITCODE -ne 0) {
            throw "uv tool install failed with exit code $LASTEXITCODE"
        }
    }
}

function Show-FreebuffInstructions {
    param(
        [string] $ProjectRoot,
        [string] $UvExe,
        [bool] $UseTool
    )
    Write-Host ""
    Write-Host "=== Freebuff (manual) ===" -ForegroundColor Yellow
    Write-Host "Freebuff stores MCP approval in ~/.freebuff/mcp.json (client-managed)."
    Write-Host "Remove and re-add the codetalker server in the Freebuff UI, then approve the consent sidecar."
    Write-Host ""
    Write-Host "Suggested launch settings:"
    if ($UseTool) {
        Write-Host "  command: codetalker"
        Write-Host "  args:    (none)"
    } else {
        Write-Host "  command: $UvExe"
        Write-Host "  args:    run --project $ProjectRoot codetalker"
    }
    Write-Host ""
    Write-Host "After re-add, verify in a new session: codetalk_capabilities (check server.project_root)."
}

$projectRoot = Resolve-ProjectRoot -Root $ProjectRoot
$projectRootNative = $projectRoot -replace "/", "\"
$uvExe = Resolve-UvCommand -Override $UvCommand
$targets = if ($Harness -contains "All") { @("Cursor", "Codex", "Antigravity", "Claude") } else { $Harness }

Write-Host "CodeTalker harness installer"
Write-Host "  ProjectRoot : $projectRoot"
Write-Host "  UvCommand   : $uvExe"
Write-Host "  Mode        : $(if ($UseUvTool) { 'uv tool (codetalker on PATH)' } else { 'uv run --project' })"
Write-Host "  Targets     : $($targets -join ', ')"
Write-Host ""

if ($UseUvTool) {
    Install-UvTool -ProjectRootNative $projectRootNative
}

$cursorEntry = New-CodetalkerEntry -ProjectRoot $projectRoot -UvExe $uvExe -UseTool:$UseUvTool -IncludeType $true
$plainEntry = New-CodetalkerEntry -ProjectRoot $projectRoot -UvExe $uvExe -UseTool:$UseUvTool -IncludeType $false

$homeDir = $env:USERPROFILE
$paths = @{
    Cursor      = Join-Path $homeDir ".cursor\mcp.json"
    Codex       = Join-Path $homeDir ".codex\config.toml"
    Antigravity = Join-Path $homeDir ".gemini\antigravity\mcp_config.json"
    Claude      = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
}

$updated = 0
if ($targets -contains "Cursor") {
    if (Merge-JsonMcpConfig -Path $paths.Cursor -CodetalkerEntry $cursorEntry -Label "Cursor") { $updated++ }
}
if ($targets -contains "Antigravity") {
    if (Merge-JsonMcpConfig -Path $paths.Antigravity -CodetalkerEntry $plainEntry -Label "Antigravity") { $updated++ }
    $altAg = Join-Path $homeDir ".gemini\config\mcp_config.json"
    if ((Test-Path -LiteralPath $altAg) -and ($altAg -ne $paths.Antigravity)) {
        if (Merge-JsonMcpConfig -Path $altAg -CodetalkerEntry $plainEntry -Label "Antigravity (config)") { $updated++ }
    }
}
if ($targets -contains "Claude") {
    if (Merge-JsonMcpConfig -Path $paths.Claude -CodetalkerEntry $plainEntry -Label "Claude") { $updated++ }
}
if ($targets -contains "Codex") {
    if (Update-CodexToml -Path $paths.Codex -ProjectRoot $projectRoot -UvExe $uvExe -UseTool:$UseUvTool) { $updated++ }
}

Show-FreebuffInstructions -ProjectRoot $projectRoot -UvExe $uvExe -UseTool:$UseUvTool

Write-Host ""
Write-Host "Done. Updated $updated config file(s)."
Write-Host "Restart each harness (or start a new Codex/Cursor session) to pick up changes."
