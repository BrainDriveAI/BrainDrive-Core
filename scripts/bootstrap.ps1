param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$ScriptDir\bootstrap_braindrive.py" @CliArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
