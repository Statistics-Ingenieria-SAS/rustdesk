#Requires -Version 5.1
<#
.SYNOPSIS
  Configura RustDesk en Windows para el servidor self-hosted Contabo.

.DESCRIPTION
  Buenas prácticas tomadas de la documentación y discusiones de RustDesk:
  - Elevación a administrador (script oficial de client-deployment).
  - Descarga de la última release Windows vía GitHub API (no scraping HTML; ver discussion #16361).
  - Instalación silenciosa MSI preferida; fallback a EXE --silent-install.
  - Escritura de RustDesk2.toml en LocalService (ruta que usa el servicio; issue #769)
    y en perfiles de usuario, con el servicio detenido.
  - Muestra ID (y contraseña opcional) para asistencia remota.

.PARAMETER ServerHost
  Host o IP del hbbs/hbbr. Por defecto: VPS Contabo.

.PARAMETER ServerKey
  Clave pública del servidor (id_ed25519.pub).

.PARAMETER SkipInstall
  Solo reescribe la configuración; no descarga ni instala.

.PARAMETER SetPassword
  Genera una contraseña permanente aleatoria y la aplica con --password.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\Configure-RustDesk-Server.ps1

.EXAMPLE
  .\Configure-RustDesk-Server.ps1 -SetPassword
#>
[CmdletBinding()]
param(
    [string]$ServerHost = '209.145.51.68',
    [string]$ServerKey = 'tjRk7Jbzv+LyhCvKZVXkUy1qzwaa8XA5USlE1lSE1vo=',
    [switch]$SkipInstall,
    [switch]$SetPassword
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# --- Elevación (patrón del script oficial RustDesk) ---
function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    Write-Host 'Se requieren permisos de administrador. Relanzando...' -ForegroundColor Yellow
    $argList = @(
        '-NoProfile'
        '-ExecutionPolicy', 'Bypass'
        '-File', "`"$PSCommandPath`""
    )
    if ($PSBoundParameters.ContainsKey('ServerHost')) { $argList += @('-ServerHost', "`"$ServerHost`"") }
    if ($PSBoundParameters.ContainsKey('ServerKey')) { $argList += @('-ServerKey', "`"$ServerKey`"") }
    if ($SkipInstall) { $argList += '-SkipInstall' }
    if ($SetPassword) { $argList += '-SetPassword' }

    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argList | Out-Null
    exit
}

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Get-RustDeskInstallDir {
    $candidates = @(
        (Join-Path $env:ProgramFiles 'RustDesk'),
        (Join-Path ${env:ProgramFiles(x86)} 'RustDesk')
    )
    foreach ($dir in $candidates) {
        if ($dir -and (Test-Path (Join-Path $dir 'rustdesk.exe'))) {
            return $dir
        }
    }
    return $null
}

function Get-WindowsCpuArch {
    # Detecta arquitectura del SO (no del proceso): x64 / x86 / arm64
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) {
        # PowerShell 32-bit corriendo en Windows 64-bit
        $arch = $env:PROCESSOR_ARCHITEW6432
    }
    switch -Regex ($arch) {
        'ARM64' { return 'arm64' }
        'AMD64' { return 'x64' }
        'x86'   { return 'x86' }
        default {
            if ([Environment]::Is64BitOperatingSystem) { return 'x64' }
            return 'x86'
        }
    }
}

function Get-LatestRustDeskWindowsAssets {
    # Best practice (discussion #16361): GitHub API en lugar de scrape del HTML de /releases/latest
    $cpuArch = Get-WindowsCpuArch
    Write-Step "Consultando última versión en GitHub (arquitectura del PC: $cpuArch)..."
    $headers = @{
        'User-Agent' = 'Configure-RustDesk-Server'
        'Accept'     = 'application/vnd.github+json'
    }
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/rustdesk/rustdesk/releases/latest' -Headers $headers

    # Assets oficiales Windows por arch (release 1.5.x):
    #   x64   → rustdesk-*-x86_64.msi / .exe
    #   arm64 → rustdesk-*-aarch64.msi / .exe
    #   x86   → rustdesk-*-x86-sciter.exe (sin MSI)
    $msi = $null
    $exe = $null
    switch ($cpuArch) {
        'x64' {
            $msi = $release.assets | Where-Object { $_.name -match '^rustdesk-[\d\.]+-x86_64\.msi$' } | Select-Object -First 1
            $exe = $release.assets | Where-Object { $_.name -match '^rustdesk-[\d\.]+-x86_64\.exe$' } | Select-Object -First 1
        }
        'arm64' {
            $msi = $release.assets | Where-Object { $_.name -match '^rustdesk-[\d\.]+-aarch64\.msi$' } | Select-Object -First 1
            $exe = $release.assets | Where-Object { $_.name -match '^rustdesk-[\d\.]+-aarch64\.exe$' } | Select-Object -First 1
        }
        'x86' {
            $exe = $release.assets | Where-Object { $_.name -match '^rustdesk-[\d\.]+-x86-sciter\.exe$' } | Select-Object -First 1
        }
    }

    if (-not $msi -and -not $exe) {
        throw "No se encontró instalador Windows ($cpuArch) en la release $($release.tag_name)."
    }

    return [pscustomobject]@{
        Version = $release.tag_name
        Arch    = $cpuArch
        Msi     = if ($msi) {
            [pscustomobject]@{ Name = $msi.name; Url = $msi.browser_download_url }
        } else { $null }
        Exe     = if ($exe) {
            [pscustomobject]@{ Name = $exe.name; Url = $exe.browser_download_url }
        } else { $null }
    }
}

function Stop-RustDeskProcesses {
    Write-Step 'Cerrando RustDesk (servicio y procesos)...'
    $svc = Get-Service -Name 'RustDesk' -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne 'Stopped') {
        Stop-Service -Name 'RustDesk' -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    Get-Process -Name 'rustdesk' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

function Uninstall-ExistingRustDesk {
    # MSI 1603 es frecuente al subir de 1.x antiguo con el servicio/app abiertos o producto previo.
    Write-Step 'Desinstalando RustDesk anterior (si existe)...'
    $products = @()
    $products += Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like 'RustDesk*' }
    $products += Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like 'RustDesk*' }

    foreach ($p in ($products | Sort-Object PSChildName -Unique)) {
        if ($p.PSChildName -match '^\{[0-9A-Fa-f\-]+\}$') {
            Write-Host "  msiexec /x $($p.PSChildName) ($($p.DisplayVersion))"
            $u = Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/x', $p.PSChildName, '/qn', '/norestart') -Wait -PassThru
            Write-Host "  código: $($u.ExitCode)"
        } elseif ($p.UninstallString) {
            Write-Host "  uninstall: $($p.UninstallString)"
            # Intento genérico; si falla seguimos con instalación encima / EXE
            try {
                cmd.exe /c "$($p.UninstallString) /S" | Out-Null
            } catch {
                Write-Warning "No se pudo desinstalar automáticamente: $($p.DisplayName)"
            }
        }
    }
    Start-Sleep -Seconds 2
}

function Install-WithMsi {
    param([string]$InstallerPath, [string]$LogPath)
    $args = @(
        '/i', "`"$InstallerPath`""
        '/qn'
        '/norestart'
        '/L*v', "`"$LogPath`""
    )
    $p = Start-Process -FilePath 'msiexec.exe' -ArgumentList $args -Wait -PassThru
    return $p.ExitCode
}

function Install-WithExe {
    param([string]$InstallerPath)
    # wait: esperado por el instalador portable/oficial en despliegues silenciosos
    $p = Start-Process -FilePath $InstallerPath -ArgumentList '--silent-install','wait' -Wait -PassThru
    Start-Sleep -Seconds 10
    return $p.ExitCode
}

function Get-InstalledRustDeskVersion {
    $uninstallKeys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\RustDesk',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\RustDesk'
    )
    foreach ($key in $uninstallKeys) {
        if (Test-Path $key) {
            $ver = (Get-ItemProperty $key -ErrorAction SilentlyContinue).DisplayVersion
            if ($ver) { return $ver }
        }
    }
    $dir = Get-RustDeskInstallDir
    if ($dir) {
        try {
            return (Get-Item (Join-Path $dir 'rustdesk.exe')).VersionInfo.ProductVersion
        } catch {
            return 'unknown'
        }
    }
    return $null
}

function Start-RustDeskApp {
    $dir = Get-RustDeskInstallDir
    if (-not $dir) {
        Write-Warning 'No se pudo abrir RustDesk: no está instalado.'
        return
    }
    $exe = Join-Path $dir 'rustdesk.exe'
    Write-Step 'Abriendo RustDesk...'
    Start-Process -FilePath $exe
}

function Install-RustDeskLatest {
    $assets = Get-LatestRustDeskWindowsAssets
    $installed = Get-InstalledRustDeskVersion
    Write-Host "Arquitectura PC:    $($assets.Arch)"
    Write-Host "Última disponible:  $($assets.Version)"
    if ($installed) {
        Write-Host "Instalada ahora:     $installed"
    }

    if ($installed -and ($installed -eq $assets.Version -or $installed.StartsWith($assets.Version))) {
        Write-Host 'Ya tiene la última versión. Se omite descarga.' -ForegroundColor Green
        return
    }

    $tempDir = Join-Path $env:TEMP 'RustDesk-Configurador'
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

    Stop-RustDeskProcesses
    if ($installed) {
        Uninstall-ExistingRustDesk
        Stop-RustDeskProcesses
    }

    $installedOk = $false

    if ($assets.Msi) {
        $msiPath = Join-Path $tempDir $assets.Msi.Name
        $msiLog = Join-Path $tempDir 'rustdesk-msi.log'
        Write-Step "Descargando $($assets.Msi.Name)..."
        Invoke-WebRequest -Uri $assets.Msi.Url -OutFile $msiPath -UseBasicParsing

        Write-Step 'Instalando RustDesk (MSI silencioso)...'
        $msiCode = Install-WithMsi -InstallerPath $msiPath -LogPath $msiLog
        if ($msiCode -in 0, 3010) {
            $installedOk = $true
        } else {
            Write-Warning "MSI falló con código $msiCode (típico 1603 si había versión vieja o archivos bloqueados)."
            Write-Warning "Log: $msiLog"
            Write-Host 'Reintentando con instalador EXE...' -ForegroundColor Yellow
        }
    }

    if (-not $installedOk) {
        if (-not $assets.Exe) {
            throw 'No hay instalador EXE de respaldo en la release.'
        }
        $exePath = Join-Path $tempDir $assets.Exe.Name
        if (-not (Test-Path $exePath)) {
            Write-Step "Descargando $($assets.Exe.Name)..."
            Invoke-WebRequest -Uri $assets.Exe.Url -OutFile $exePath -UseBasicParsing
        }
        Stop-RustDeskProcesses
        Write-Step 'Instalando RustDesk (EXE --silent-install)...'
        $exeCode = Install-WithExe -InstallerPath $exePath
        if ($null -ne $exeCode -and $exeCode -notin 0, $null) {
            Write-Warning "El instalador EXE terminó con código $exeCode. Se continúa si rustdesk.exe existe."
        }
    }

    $dir = Get-RustDeskInstallDir
    if (-not $dir) {
        throw 'No se encontró RustDesk tras la instalación.'
    }

    $svc = Get-Service -Name 'RustDesk' -ErrorAction SilentlyContinue
    if (-not $svc) {
        Write-Step 'Instalando servicio RustDesk...'
        Push-Location $dir
        try {
            & .\rustdesk.exe --install-service
            Start-Sleep -Seconds 5
        } finally {
            Pop-Location
        }
    }
}

function Get-RustDeskConfigToml {
    param([string]$HostName, [string]$Key)

    # Relay en el mismo host: la comunidad suele dejarlo explícito o vacío.
    # Lo dejamos explícito para evitar caer al relay público.
    @"
rendezvous_server = '$HostName:21116'
nat_type = 1
serial = 0
unlock_pin = ''
trusted_devices = ''

[options]
custom-rendezvous-server = '$HostName'
relay-server = '$HostName'
key = '$Key'
direct-server = 'Y'
"@
}

function Get-RustDeskConfigPaths {
    $paths = New-Object System.Collections.Generic.List[string]

    $servicePaths = @(
        'C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\config\RustDesk2.toml',
        'C:\Windows\System32\config\systemprofile\AppData\Roaming\RustDesk\config\RustDesk2.toml',
        'C:\ProgramData\RustDesk\config\RustDesk2.toml'
    )
    foreach ($p in $servicePaths) { $paths.Add($p) }

    # Perfil actual
    $paths.Add((Join-Path $env:APPDATA 'RustDesk\config\RustDesk2.toml'))

    # Perfiles de usuario existentes (Intune / RMM practice)
    $usersRoot = 'C:\Users'
    if (Test-Path $usersRoot) {
        Get-ChildItem $usersRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notin @('Public', 'Default', 'Default User', 'All Users') } |
            ForEach-Object {
                $paths.Add((Join-Path $_.FullName 'AppData\Roaming\RustDesk\config\RustDesk2.toml'))
            }
        $paths.Add((Join-Path $usersRoot 'Default\AppData\Roaming\RustDesk\config\RustDesk2.toml'))
    }

    return $paths | Select-Object -Unique
}

function Stop-RustDeskServiceSafe {
    $svc = Get-Service -Name 'RustDesk' -ErrorAction SilentlyContinue
    if (-not $svc) { return }
    if ($svc.Status -ne 'Stopped') {
        Write-Step 'Deteniendo servicio RustDesk...'
        Stop-Service -Name 'RustDesk' -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

function Start-RustDeskServiceSafe {
    $svc = Get-Service -Name 'RustDesk' -ErrorAction SilentlyContinue
    if (-not $svc) {
        Write-Warning 'Servicio RustDesk no encontrado. Abra RustDesk una vez para registrarlo.'
        return
    }
    Write-Step 'Iniciando servicio RustDesk...'
    Start-Service -Name 'RustDesk' -ErrorAction SilentlyContinue
    $retries = 0
    while ($retries -lt 6) {
        $svc.Refresh()
        if ($svc.Status -eq 'Running') { return }
        Start-Sleep -Seconds 2
        Start-Service -Name 'RustDesk' -ErrorAction SilentlyContinue
        $retries++
    }
    Write-Warning 'El servicio no quedó en Running. Revise services.msc.'
}

function Write-RustDeskServerConfig {
    param([string]$HostName, [string]$Key)

    $toml = Get-RustDeskConfigToml -HostName $HostName -Key $Key
    $targets = Get-RustDeskConfigPaths

    Write-Step 'Escribiendo RustDesk2.toml (LocalService + perfiles)...'
    foreach ($path in $targets) {
        $dir = Split-Path -Parent $path
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
        }
        # UTF8 sin BOM: evita problemas de parseo TOML en algunos builds
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($path, $toml, $utf8NoBom)
        Write-Host "  OK  $path"
    }
}

function Get-RustDeskId {
    $dir = Get-RustDeskInstallDir
    if (-not $dir) { return $null }
    Push-Location $dir
    try {
        $id = (& .\rustdesk.exe --get-id 2>$null | Out-String).Trim()
        if ($id) { return ($id -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 1).Trim() }
    } catch {
        return $null
    } finally {
        Pop-Location
    }
    return $null
}

function Set-RustDeskPermanentPassword {
    param([string]$Password)
    $dir = Get-RustDeskInstallDir
    if (-not $dir) { return }
    Push-Location $dir
    try {
        & .\rustdesk.exe --password $Password | Out-Null
    } finally {
        Pop-Location
    }
}

# ---------------- Main ----------------
Write-Host '=============================================' -ForegroundColor Green
Write-Host '  Servidor Statistics Ingeniería S.A.S.' -ForegroundColor Green
Write-Host '  Configurador RustDesk' -ForegroundColor Green
Write-Host '=============================================' -ForegroundColor Green
Write-Host "Servidor: $ServerHost"
Write-Host "Key:      $ServerKey"

try {
    if (-not $SkipInstall) {
        Install-RustDeskLatest
    } elseif (-not (Get-RustDeskInstallDir)) {
        throw 'RustDesk no está instalado. Ejecute sin -SkipInstall para descargar la última versión.'
    }

    Stop-RustDeskServiceSafe
    Write-RustDeskServerConfig -HostName $ServerHost -Key $ServerKey
    Start-RustDeskServiceSafe

    # Dar tiempo a que el servicio lea la config y registre ID
    Start-Sleep -Seconds 3

    $password = $null
    if ($SetPassword) {
        $password = -join ((65..90) + (97..122) + (48..57) | Get-Random -Count 12 | ForEach-Object { [char]$_ })
        Write-Step 'Aplicando contraseña permanente...'
        Set-RustDeskPermanentPassword -Password $password
    }

    $rustdeskId = Get-RustDeskId

    Write-Host ''
    Write-Host '=============================================' -ForegroundColor Green
    Write-Host '  Servidor Statistics Ingeniería S.A.S.' -ForegroundColor Green
    Write-Host '  LISTO — envíe estos datos a soporte' -ForegroundColor Green
    Write-Host '=============================================' -ForegroundColor Green
    if ($rustdeskId) {
        Write-Host "RustDesk ID: $rustdeskId" -ForegroundColor Yellow
    } else {
        Write-Host 'RustDesk ID: (abra RustDesk y cópielo de la ventana principal)' -ForegroundColor Yellow
    }
    if ($password) {
        Write-Host "Password:    $password" -ForegroundColor Yellow
    }
    Write-Host "Servidor:    $ServerHost"
    Write-Host ''
    Write-Host 'Si el estado no dice Ready, cierre RustDesk por completo (bandeja) y ábralo de nuevo.'

    Start-RustDeskApp

    Write-Host ''
    Write-Host 'Presione Enter para cerrar...'
    [void][System.Console]::ReadLine()
}
catch {
    Write-Host ''
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Presione Enter para cerrar...'
    [void][System.Console]::ReadLine()
    exit 1
}
