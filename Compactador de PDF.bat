@echo off
setlocal
cd /d "%~dp0"
title Compactador de PDF

if not exist "app.py" (
    echo ================================================================
    echo   Nao encontrei o arquivo app.py nesta pasta.
    echo ================================================================
    echo.
    echo Isso normalmente acontece quando o programa e aberto direto de
    echo dentro do arquivo compactador_pdf.zip, sem extrair antes.
    echo.
    echo SOLUCAO:
    echo   1. Feche esta janela.
    echo   2. Clique com o botao DIREITO em "compactador_pdf.zip".
    echo   3. Escolha "Extrair Tudo..." ^(Extract All...^).
    echo   4. Abra a PASTA EXTRAIDA e rode o .bat de dentro dela.
    echo.
    pause
    exit /b 1
)

rem "Caminho rapido": se o programa ja foi configurado antes (venv existe),
rem pula direto para abrir a janela, sem mostrar nada na tela. Isso e o que
rem faz o "Abrir Compactador de PDF.vbs" parecer um programa normal, sem
rem nenhuma janela preta aparecendo.
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" "app.py"
    exit /b 0
)

echo %CD% | findstr /I "\Temp\" >nul
if not errorlevel 1 (
    echo ================================================================
    echo   Aviso: esta pasta parece estar dentro de uma pasta temporaria
    echo   do Windows ^(Temp^). Se voce abriu o .bat direto de dentro do
    echo   .zip sem extrair, isso pode causar erros estranhos.
    echo.
    echo   Se algo der errado, extraia o .zip para uma pasta normal
    echo   ^(ex: Area de Trabalho^) e tente de novo.
    echo ================================================================
    echo.
)

where python >nul 2>nul
if errorlevel 1 (
    echo ================================================================
    echo   Python nao foi encontrado no seu computador
    echo ================================================================
    echo.
    echo Para usar este programa e necessario instalar o Python uma vez:
    echo.
    echo   1. Acesse https://www.python.org/downloads/
    echo   2. Baixe e instale a versao mais recente
    echo   3. IMPORTANTE: marque a caixa "Add Python to PATH" durante a instalacao
    echo   4. Depois de instalar, execute este arquivo novamente ^(duplo clique^)
    echo.
    pause
    exit /b 1
)

python --version >nul 2>nul
if errorlevel 1 (
    echo ================================================================
    echo   O comando "python" existe mas nao funciona corretamente.
    echo ================================================================
    echo.
    echo Isso costuma acontecer quando o Windows tem apenas o "atalho" da
    echo Microsoft Store para o Python instalado ^(nao o Python de verdade^).
    echo.
    echo SOLUCAO:
    echo   1. Abra "Configuracoes" no Windows, va em "Aplicativos" e
    echo      procure por "python" - se existir uma entrada vindo da
    echo      Microsoft Store, desinstale-a.
    echo   2. Acesse https://www.python.org/downloads/ e instale o Python
    echo      por la, marcando "Add Python to PATH" na instalacao.
    echo   3. Execute este arquivo novamente.
    echo.
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo Preparando o programa pela primeira vez, aguarde um instante...
    echo ^(isso so acontece uma vez; nas proximas vezes o programa abre direto^)
    echo.
    python -m venv venv
    if errorlevel 1 (
        echo.
        echo ================================================================
        echo   Nao consegui criar o ambiente do programa ^(venv^).
        echo   Copie a mensagem de erro acima e envie para quem te ajudou
        echo   a configurar o programa.
        echo ================================================================
        pause
        exit /b 1
    )
    call "venv\Scripts\activate.bat"
    python -m pip install --upgrade pip >nul 2>nul
    pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ================================================================
        echo   Ocorreu um erro ao instalar os componentes necessarios.
        echo   Verifique sua conexao com a internet e tente novamente.
        echo   Copie a mensagem de erro acima e envie para quem te ajudou
        echo   a configurar o programa.
        echo ================================================================
        pause
        exit /b 1
    )
    echo.
    echo Preparacao concluida!
    echo.
)

echo Abrindo o programa...
echo (esta janela pode ficar aberta atras do programa - e normal)
echo.
"venv\Scripts\python.exe" "app.py"
set EXITCODE=%errorlevel%

if not "%EXITCODE%"=="0" (
    echo.
    echo ================================================================
    echo   O programa fechou com um erro ^(codigo %EXITCODE%^).
    echo   Se nao apareceu nenhuma mensagem de erro em uma janela, o
    echo   detalhe foi salvo no arquivo "erro.log" nesta mesma pasta.
    echo   Envie a mensagem acima ^(ou o erro.log^) para quem te ajudou a
    echo   configurar o programa.
    echo ================================================================
    pause
)
