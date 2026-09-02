# Start all microservices and Flask app

Write-Host "Starting Chakora Hub Services..." -ForegroundColor Green

# ALWAYS start from THIS script's location
$scriptDir = $PSScriptRoot
Set-Location $scriptDir
$pythonExe = Join-Path $scriptDir "venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    Write-Host "ERROR: virtual environment Python not found at $pythonExe" -ForegroundColor Red
    exit 1
}

Write-Host "Working directory: $scriptDir" -ForegroundColor DarkGray

# Set environment variables
$env:STUDENT_SERVICE_URL="http://localhost:8001"
$env:EMPLOYEE_SERVICE_URL="http://localhost:8002"
$env:HOME_SERVICE_URL="http://localhost:5001"
$env:RESOURCES_SERVICE_URL="http://localhost:5002"


# Kill any existing processes on these ports
Write-Host "Cleaning up existing processes..." -ForegroundColor Yellow

# Find and kill processes on ports 8001, 8002, 5001, 5002, 5050, 8080
$ports = @(8001, 8002, 5001, 5002, 5050, 8080)
foreach ($port in $ports) {
    $process = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($process) {
        $pidToKill = $process.OwningProcess
        Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped process on port $port (PID: $pidToKill)"
    }
}

Start-Sleep -Seconds 2

# Check if Python files exist
Write-Host "Checking for Python files..." -ForegroundColor DarkGray
$requiredFiles = @("student_service.py", "employee_service.py", "home_service.py", "app.py", "internship_service.py")
foreach ($file in $requiredFiles) {
    if (-not (Test-Path $file)) {
        Write-Host "ERROR: $file not found in current directory!" -ForegroundColor Red
        Write-Host "Current directory: $(Get-Location)" -ForegroundColor Red
        exit 1
    }
}

# Start Student Service (Port 8001)
Write-Host "Starting Student Service on port 8001..." -ForegroundColor Cyan
$studentProc = Start-Process $pythonExe -ArgumentList "student_service.py" -PassThru -NoNewWindow
Write-Host "Student Service PID: $($studentProc.Id)"

# Wait for service to start
Start-Sleep -Seconds 3

# Start Employee Service (Port 8002)  
Write-Host "Starting Employee Service on port 8002..." -ForegroundColor Cyan
$employeeProc = Start-Process $pythonExe -ArgumentList "employee_service.py" -PassThru -NoNewWindow
Write-Host "Employee Service PID: $($employeeProc.Id)"

# Wait for service to start
Start-Sleep -Seconds 3

# Start Home Service (Port 5001) 
Write-Host "Starting Home Service on port 5001..." -ForegroundColor Cyan
$homeProc = Start-Process $pythonExe -ArgumentList "home_service.py" -PassThru -NoNewWindow
Write-Host "Home Service PID: $($homeProc.Id)"

# Wait for service to start
Start-Sleep -Seconds 3

# Start Resources Service (Port 5002) - OPTIONAL
if (Test-Path "student_resources.py") {
    Write-Host "Starting Resources Service on port 5002..." -ForegroundColor Cyan
    $resourcesProc = Start-Process $pythonExe -ArgumentList "student_resources.py" -PassThru -NoNewWindow
    Write-Host "Resources Service PID: $($resourcesProc.Id)"
    Start-Sleep -Seconds 3
}

# Start Internship Service (Port 5050)
Write-Host "Starting Internship Service on port 5050..." -ForegroundColor Cyan
$internshipProc = Start-Process $pythonExe -ArgumentList "internship_service.py" -PassThru -NoNewWindow
Write-Host "Internship Service PID: $($internshipProc.Id)"

# Wait for service to start
Start-Sleep -Seconds 3

# Start Flask Proxy (Port 8080)
Write-Host "Starting Flask Proxy on port 8080..." -ForegroundColor Cyan
$flaskProc = Start-Process $pythonExe -ArgumentList "app.py" -PassThru -NoNewWindow
Write-Host "Flask Proxy PID: $($flaskProc.Id)"

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "✅ All services started!" -ForegroundColor Green
Write-Host ""
Write-Host "Services:"
Write-Host "  - Student Service:    http://localhost:8001 (PID: $($studentProc.Id))"
Write-Host "  - Employee Service:   http://localhost:8002 (PID: $($employeeProc.Id))"
Write-Host "  - Home Service:       http://localhost:5001 (PID: $($homeProc.Id))"
if (Test-Path "student_resources.py") {
    Write-Host "  - Resources Service:  http://localhost:5002 (PID: $($resourcesProc.Id))"
}
Write-Host "  - Internship Service: http://localhost:5050 (PID: $($internshipProc.Id))"
Write-Host "  - Flask Proxy:        http://localhost:8080 (PID: $($flaskProc.Id))"
Write-Host ""
Write-Host "Health checks:"
Write-Host "  - curl http://localhost:8001/health"
Write-Host "  - curl http://localhost:8002/health"
Write-Host "  - curl http://localhost:5050/health"
Write-Host ""
Write-Host "Main Application:"
Write-Host "  - Open: http://localhost:8080"
Write-Host ""
Write-Host "Press Enter to stop all services"
Write-Host ""

# Wait for user to press Enter
Read-Host "Press Enter to stop all services"

# Clean up
Write-Host "[STOP] Stopping all services..." -ForegroundColor Red
Stop-Process -Id $studentProc.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $employeeProc.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $homeProc.Id -Force -ErrorAction SilentlyContinue
if (Test-Path "student_resources.py") {
    Stop-Process -Id $resourcesProc.Id -Force -ErrorAction SilentlyContinue
}
Stop-Process -Id $internshipProc.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $flaskProc.Id -Force -ErrorAction SilentlyContinue
Write-Host "All services stopped." -ForegroundColor Green