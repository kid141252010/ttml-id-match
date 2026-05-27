param(
    [string]$TargetDir,
    [int]$SearchWorkers = 3,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

function Wait-BeforeExit {
    if (-not $NoPause) {
        Write-Host ""
        Read-Host "Press Enter to exit"
    }
}

function Exit-WithCode {
    param([int]$Code)
    Wait-BeforeExit
    exit $Code
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonScript = Join-Path $scriptDir "fill_ttml_metadata.py"

try {
    if (-not (Test-Path -LiteralPath $pythonScript -PathType Leaf)) {
        Write-Host "[ERROR] Missing script: $pythonScript"
        Exit-WithCode 1
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        Write-Host "[ERROR] python command was not found. Install Python 3.10 or newer first."
        Exit-WithCode 1
    }

    if ([string]::IsNullOrWhiteSpace($TargetDir)) {
        $TargetDir = Read-Host "Enter the directory to process"
    }

    $TargetDir = $TargetDir.Trim().Trim('"')
    if ([string]::IsNullOrWhiteSpace($TargetDir)) {
        Write-Host "[ERROR] Directory is required."
        Exit-WithCode 1
    }

    try {
        $resolvedTarget = Resolve-Path -LiteralPath $TargetDir -ErrorAction Stop
    } catch {
        Write-Host "[ERROR] Directory does not exist: $TargetDir"
        Exit-WithCode 1
    }

    $targetItem = Get-Item -LiteralPath $resolvedTarget.Path
    if (-not $targetItem.PSIsContainer) {
        Write-Host "[ERROR] Target is not a directory: $($resolvedTarget.Path)"
        Exit-WithCode 1
    }

    if ($SearchWorkers -lt 1) {
        Write-Host "[ERROR] SearchWorkers must be at least 1."
        Exit-WithCode 1
    }

    & python $pythonScript $resolvedTarget.Path --dry-run --search-workers $SearchWorkers
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "[ERROR] dry-run failed. Fix the errors above and try again."
        Exit-WithCode $LASTEXITCODE
    }

    Write-Host ""
    $confirm = Read-Host "Type Y to write changes"
    if ($confirm -ieq "Y") {
        Write-Host ""
        Write-Host "[WRITE] Updating TTML files. The Python script will create .bak backups."
        & python $pythonScript $resolvedTarget.Path --search-workers $SearchWorkers
        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "[ERROR] write failed. Fix the errors above and try again."
            Exit-WithCode $LASTEXITCODE
        }

        Write-Host ""
        Write-Host "Finished."
    } else {
        Write-Host ""
        Write-Host "Cancelled. No files were modified."
    }

    Exit-WithCode 0
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    Exit-WithCode 1
}
