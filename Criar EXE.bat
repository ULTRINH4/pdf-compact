@echo off
setlocal
cd /d "%~dp0"
title Gerando o .exe do Compactador de PDF

if not exist "venv\Scripts\python.exe" (
    echo ================================================================
    echo   O programa ainda nao foi configurado neste computador.
    echo ================================================================
    echo.
    echo Antes de gerar o .exe, rode pelo menos uma vez o arquivo
    echo "Compactador de PDF.bat" ^(ou o "Abrir Compactador de PDF.vbs"^)
    echo para instalar os componentes necessarios. Depois volte e rode
    echo este arquivo de novo.
    echo.
    pause
    exit /b 1
)

echo ================================================================
echo   Gerando um arquivo .exe independente do Compactador de PDF
echo ================================================================
echo.
echo Isso cria um "Compactador de PDF.exe" que funciona sozinho, sem
echo precisar do Python instalado - voce podera mover so esse arquivo
echo .exe para qualquer lugar (ex: Area de Trabalho) e apagar o resto.
echo.
echo Instalando a ferramenta que gera o .exe (PyInstaller)...
"venv\Scripts\python.exe" -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo.
    echo ================================================================
    echo   Erro ao instalar o PyInstaller. Verifique sua conexao com a
    echo   internet e tente novamente.
    echo ================================================================
    pause
    exit /b 1
)

echo.
echo Gerando o arquivo .exe, pode levar 1 a 2 minutos...
echo.
"venv\Scripts\python.exe" -m PyInstaller --onefile --windowed --noconfirm --noupx ^
    --version-file "version_info.txt" --name "Compactador de PDF" app.py
if errorlevel 1 (
    echo.
    echo ================================================================
    echo   Ocorreu um erro ao gerar o .exe. Copie a mensagem acima e
    echo   envie para quem te ajudou a configurar o programa.
    echo ================================================================
    pause
    exit /b 1
)

echo.
echo ================================================================
echo   PRONTO!
echo.
echo   O arquivo "Compactador de PDF.exe" foi criado dentro da pasta
echo   "dist" ^(que esta aqui do lado^).
echo.
echo   Voce pode:
echo     - Mover esse .exe para a Area de Trabalho (ou onde preferir)
echo     - Depois disso, pode ate apagar o resto desta pasta - o .exe
echo       funciona sozinho, sem precisar do Python instalado
echo ================================================================
echo.
pause
