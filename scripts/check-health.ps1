param(
    [int]$Port = 8000,
    [int]$MaxAttempts = 20
)

$ErrorActionPreference = "Stop"
$baseUrl = "http://127.0.0.1:$Port"
$healthUrl = "$baseUrl/api/health/"
$server = $null

try {
    $server = Start-Process -FilePath "py" `
        -ArgumentList "-3", "manage.py", "runserver", "127.0.0.1:$Port", "--noreload" `
        -PassThru -WindowStyle Hidden

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            $response = Invoke-RestMethod -Uri $healthUrl -Method Get
            if ($response.status -eq "ok") {
                Write-Output "Health check passed: $healthUrl"
                exit 0
            }
        }
        catch {
            if ($server.HasExited) {
                throw "Django server stopped before the health route became available."
            }
        }

        Start-Sleep -Milliseconds 500
    }

    throw "Health route did not respond successfully after $MaxAttempts attempts."
}
finally {
    if ($null -ne $server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
}