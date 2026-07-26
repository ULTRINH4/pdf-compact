@echo off
setlocal
cd /d "%~dp0"
title Publicar Nova Versao - Compactador de PDF

echo ================================================================
echo   Publicar uma nova versao do Compactador de PDF
echo ================================================================
echo.
echo Isso vai:
echo   1. Atualizar o numero da versao dentro do programa (app.py)
echo   2. Enviar essa mudanca para o GitHub
echo   3. O proprio GitHub vai compilar o .exe novo e publicar
echo      automaticamente (leva 2 a 5 minutos, sem voce precisar
echo      fazer mais nada)
echo.
echo   Os usuarios que ja tem o programa instalado vao receber um
echo   aviso de atualizacao sozinhos, na proxima vez que abrirem.
echo.
set /p NOVAVERSAO="Digite o numero da nova versao (ex: 1.0.1): "

if "%NOVAVERSAO%"=="" (
    echo Nenhuma versao informada. Cancelado.
    pause
    exit /b 1
)

echo.
echo Atualizando app.py para a versao %NOVAVERSAO%...
powershell -NoProfile -Command "(Get-Content 'app.py' -Raw) -replace 'APP_VERSION = \"[^\"]*\"', 'APP_VERSION = \"%NOVAVERSAO%\"' | Set-Content -NoNewline -Encoding utf8 'app.py'"
if errorlevel 1 (
    echo.
    echo ================================================================
    echo   Nao consegui atualizar o numero da versao em app.py.
    echo ================================================================
    pause
    exit /b 1
)

git add app.py
git commit -m "Versao %NOVAVERSAO%"

git tag "v%NOVAVERSAO%"
if errorlevel 1 (
    echo.
    echo ================================================================
    echo   Ja existe uma tag "v%NOVAVERSAO%". Escolha um numero de
    echo   versao que ainda nao foi usado.
    echo ================================================================
    pause
    exit /b 1
)

git push
git push origin "v%NOVAVERSAO%"
if errorlevel 1 (
    echo.
    echo ================================================================
    echo   Ocorreu um erro ao enviar para o GitHub. Verifique sua
    echo   conexao com a internet e se voce esta autenticado
    echo   ^(gh auth status^), e tente novamente.
    echo ================================================================
    pause
    exit /b 1
)

echo.
echo ================================================================
echo   Pronto! O GitHub esta compilando a versao %NOVAVERSAO% agora.
echo.
echo   Acompanhe em:
echo   https://github.com/ULTRINH4/pdf-compact/actions
echo.
echo   Quando terminar ^(2 a 5 minutos^), quem ja tem o programa
echo   instalado vai ver o aviso de atualizacao sozinho, na proxima
echo   vez que abrir.
echo ================================================================
pause
