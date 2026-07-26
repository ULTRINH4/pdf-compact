#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Compactador de PDF e Conversor de Fotos em PDF
------------------------------------------------
Programa de mesa (desktop) para:
  1) Compactar arquivos PDF até ficarem abaixo de um tamanho alvo escolhido
     pelo usuário (12 MB, 5 MB, ou outro valor customizado).
  2) Transformar fotos (JPG, PNG, HEIC, etc.) em arquivos PDF individuais,
     um PDF separado para cada foto — conversão direta, sem opções de
     tamanho/compactação (as fotos raramente ultrapassam o limite).
  3) Juntar vários PDFs, na ordem escolhida pelo usuário, em um único
     arquivo PDF final (nunca dividido em partes).

Como funciona a compactação:
  - Primeiro tenta uma limpeza "sem perdas" do PDF (remove dados redundantes).
  - Se ainda estiver acima do alvo, reduz progressivamente a qualidade e a
    resolução das IMAGENS dentro do PDF (mantendo o texto vetorial intacto
    sempre que possível), até um limite máximo que preserva a legibilidade.
  - Se mesmo assim não couber no alvo, divide o PDF em partes numeradas.
  - Vários arquivos podem ser compactados ao mesmo tempo (em paralelo).

Como funciona o "Juntar PDF":
  - O usuário escolhe os PDFs e a ordem final (reordenável na lista).
  - Os arquivos são concatenados nessa ordem em um único PDF de saída —
    esta função sempre produz exatamente 1 arquivo, nunca divide em partes.

Autor: gerado com Claude para o projeto "compactador de pdf"
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _erro_log_path():
    try:
        base = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base = os.getcwd()
    return os.path.join(base, "erro.log")


def _fatal_error(title, message, details=""):
    """Mostra um erro de um jeito que o usuário SEMPRE vai ver, mesmo que
    o programa esteja rodando sem console visível (pythonw) ou que o
    próprio Tkinter esteja quebrado. Também grava em erro.log para
    diagnóstico posterior."""
    full_text = message + (("\n\nDetalhes técnicos:\n" + details) if details else "")
    print(f"=== {title} ===\n{full_text}", file=sys.stderr)
    try:
        with open(_erro_log_path(), "w", encoding="utf-8") as f:
            f.write(f"{title}\n\n{full_text}\n")
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, full_text, title, 0x10)  # MB_ICONERROR
    except Exception:
        pass  # não é Windows, ou ctypes indisponível — o console/erro.log já cobrem o caso


try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
except ImportError as e:
    _fatal_error(
        "Compactador de PDF - componente ausente",
        "Este programa precisa do Tkinter, que normalmente já vem junto com "
        "o Python, mas não foi encontrado na sua instalação.\n\n"
        "Solução: baixe o instalador do Python novamente em "
        "https://www.python.org/downloads/, rode-o e escolha a opção "
        "'Modify' (ou desinstale e instale de novo). Certifique-se de que "
        "a opção 'tcl/tk and IDLE' esteja marcada durante a instalação "
        "(ela vem marcada por padrão — só costuma faltar em instalações "
        "customizadas).",
        details=str(e),
    )
    sys.exit(1)

try:
    import pikepdf
    from pikepdf import Name
except ImportError:
    pikepdf = None
    Name = None

try:
    from PIL import Image
except ImportError:
    Image = None

# HEIC/HEIF support (fotos de iPhone) - opcional
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

APP_TITLE = "Compactador de PDF e Fotos em PDF"
DEFAULT_TARGET_MB = 12.0

IMAGE_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff",
    ".heic", ".heif", ".webp", ".gif",
)

# --------------------------------------------------------------------------
# Verificação de atualizações (via GitHub Releases)
# --------------------------------------------------------------------------
# Suba de versão aqui sempre que publicar uma versão nova (veja também
# "Publicar Nova Versao.bat").
APP_VERSION = "1.0.1"
_UPDATE_GITHUB_OWNER = "ULTRINH4"
_UPDATE_GITHUB_REPO = "pdf-compact"
# Nome só do arquivo temporário baixado — não precisa bater com o nome do
# asset no GitHub. O GitHub troca espaços por pontos no nome dos assets de
# Release (ex: "Compactador de PDF.exe" vira "Compactador.de.PDF.exe"), então
# a busca abaixo pega o primeiro asset ".exe" em vez de comparar nomes.
_UPDATE_ASSET_LOCAL_NAME = "Compactador de PDF.exe"


def _parse_version(text):
    """Converte "v1.2.3" (ou "1.2.3") em (1, 2, 3) para comparar versões.
    Tolerante a formatos inesperados: pedaços não numéricos viram 0."""
    text = (text or "").strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    parts = []
    for piece in text.split(".")[:3]:
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _fetch_latest_release_info():
    url = (f"https://api.github.com/repos/{_UPDATE_GITHUB_OWNER}/"
           f"{_UPDATE_GITHUB_REPO}/releases/latest")
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "CompactadorPDF-Updater",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_for_update_in_background(on_update_found):
    """Roda em thread separada: consulta o último release no GitHub e, se
    for mais novo que a versão atual, chama on_update_found(versao, url).
    Nunca levanta exceção — checagem de atualização é "best effort" e não
    pode travar o programa nem exigir internet para funcionar."""

    def _worker():
        # Só faz sentido checar/baixar update quando rodando como .exe
        # empacotado (PyInstaller) — rodando via "python app.py" o próprio
        # arquivo .py é que estaria sendo executado, e não dá pra substituir
        # um script em edição da mesma forma seguramente.
        if not getattr(sys, "frozen", False):
            return
        try:
            release = _fetch_latest_release_info()
            latest_tag = release.get("tag_name", "")
            if _parse_version(latest_tag) <= _parse_version(APP_VERSION):
                return
            asset_url = None
            for asset in release.get("assets", []):
                if asset.get("name", "").lower().endswith(".exe"):
                    asset_url = asset.get("browser_download_url")
                    break
            if not asset_url:
                return
            on_update_found(latest_tag.lstrip("vV"), asset_url)
        except Exception:
            pass  # sem internet, GitHub fora do ar, etc. — ignora silenciosamente

    threading.Thread(target=_worker, daemon=True).start()


def download_and_apply_update(asset_url):
    """Baixa o novo .exe, prepara um script auxiliar que troca o arquivo
    assim que este processo terminar, e relança o programa na versão nova.
    Levanta exceção em caso de erro (o chamador decide como avisar o
    usuário)."""
    current_exe = os.path.abspath(sys.executable)
    tmp_dir = tempfile.mkdtemp(prefix="pdfcompact_update_")
    new_exe_path = os.path.join(tmp_dir, _UPDATE_ASSET_LOCAL_NAME)

    req = urllib.request.Request(asset_url, headers={"User-Agent": "CompactadorPDF-Updater"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(new_exe_path, "wb") as f:
        shutil.copyfileobj(resp, f)

    bat_path = os.path.join(tmp_dir, "aplicar_update.bat")
    bat_contents = (
        "@echo off\r\n"
        "setlocal\r\n"
        "set PID=%1\r\n"
        ":wait\r\n"
        "tasklist /fi \"PID eq %PID%\" 2>nul | find \"%PID%\" >nul\r\n"
        "if not errorlevel 1 (\r\n"
        "    timeout /t 1 /nobreak >nul\r\n"
        "    goto wait\r\n"
        ")\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        f"move /y \"{new_exe_path}\" \"{current_exe}\" >nul\r\n"
        f"start \"\" \"{current_exe}\"\r\n"
        "del \"%~f0\"\r\n"
    )
    # Arquivos .bat do Windows esperam o codepage ANSI/OEM local, não UTF-8 —
    # os caminhos aqui só têm acentos/portugues nos nomes de pasta do
    # usuário, então "mbcs" (codepage padrão do Windows) é o mais seguro.
    with open(bat_path, "w", encoding="mbcs") as f:
        f.write(bat_contents)

    subprocess.Popen(
        ["cmd", "/c", bat_path, str(os.getpid())],
        creationflags=subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )


# --------------------------------------------------------------------------
# Lógica principal (sem interface) — pode ser reaproveitada/testada isolada
# --------------------------------------------------------------------------

# Combinações (qualidade JPEG %, escala de resolução) em ordem crescente de
# agressividade. Mantida curta de propósito: cada combinação testada exige
# reabrir e regravar o PDF inteiro, então quanto menos tentativas, mais
# rápido o programa responde.
#
# O último nível (52%, 78%) é o limite máximo de agressividade permitido —
# escolhido para não deixar texto/imagens digitalizadas ilegíveis. Se
# mesmo nesse nível o arquivo continuar acima do tamanho desejado, o
# programa NÃO compacta mais que isso; em vez disso, divide o PDF em
# partes (veja compress_and_maybe_split).
_COMPRESSION_LEVELS = [
    (92, 1.0),
    (82, 1.0),
    (72, 0.9),
    (62, 0.85),
    (52, 0.78),
]

# Imagens menores que isso (bytes, tamanho já comprimido dentro do PDF) não
# costumam pesar no total, então são deixadas como estão — processá-las só
# gastaria tempo sem reduzir o arquivo de forma perceptível.
_MIN_IMAGE_BYTES_TO_PROCESS = 15 * 1024

_MAX_WORKERS = min(8, (os.cpu_count() or 4))

# Quantos arquivos podem ser compactados AO MESMO TEMPO (em paralelo). O
# trabalho pesado de cada um (recompactar imagens) usa o pool COMPARTILHADO
# abaixo, então processar vários arquivos ao mesmo tempo não sobrecarrega o
# processador além do que já era usado para um arquivo só — só faz melhor
# uso dos núcleos disponíveis enquanto cada arquivo passa por etapas que não
# usam CPU o tempo todo (leitura de disco, gravação, etc.).
_MAX_CONCURRENT_FILES = max(2, min(4, (os.cpu_count() or 4)))

# Pool de threads ÚNICO e COMPARTILHADO para recompactar imagens, usado por
# todos os arquivos sendo processados, mesmo quando vários rodam em
# paralelo. Isso evita que processar N arquivos ao mesmo tempo crie N pools
# concorrendo pelos mesmos núcleos (o que deixaria tudo mais lento em vez de
# mais rápido) — o número total de imagens sendo recompactadas ao mesmo
# tempo, em toda a aplicação, nunca passa de _MAX_WORKERS.
_IMAGE_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_WORKERS)


def get_size_mb(path):
    return os.path.getsize(path) / (1024 * 1024)


def _pick_start_level(ratio_needed):
    """Escolhe em qual combinação da lista _COMPRESSION_LEVELS começar,
    com base em quanto ainda falta reduzir (ratio_needed = alvo/atual).
    Evita perder tempo testando reduções muito fracas quando já se sabe
    que vai precisar de muito mais compactação, e vice-versa."""
    if ratio_needed >= 0.8:
        return 0
    if ratio_needed >= 0.62:
        return 1
    if ratio_needed >= 0.48:
        return 2
    if ratio_needed >= 0.35:
        return 3
    return 4


def _collect_significant_images(input_path, log):
    """Abre o PDF uma única vez, localiza as imagens embutidas relevantes
    (acima do limiar de tamanho) e decodifica cada uma UMA ÚNICA VEZ,
    guardando o resultado em memória. Isso evita redecodificar a mesma
    imagem a cada tentativa de compactação, que era o maior gargalo de
    performance da versão anterior.

    Retorna uma lista de dicionários: {"page_idx", "pos", "pil_image"}.
    """
    significant = []
    total_images = 0
    with pikepdf.open(input_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            for pos, (_name, raw_image) in enumerate(dict(page.images).items()):
                total_images += 1
                try:
                    raw_len = len(raw_image.read_raw_bytes())
                except Exception:
                    raw_len = _MIN_IMAGE_BYTES_TO_PROCESS  # trata como relevante se não der pra medir

                if raw_len < _MIN_IMAGE_BYTES_TO_PROCESS:
                    continue

                try:
                    pdf_image = pikepdf.PdfImage(raw_image)
                    pil_img = pdf_image.as_pil_image()
                except Exception:
                    # Formato não suportado (ex: JBIG2) — deixamos como está.
                    continue

                if pil_img.mode not in ("RGB", "L"):
                    pil_img = pil_img.convert("RGB")

                significant.append({
                    "page_idx": page_idx,
                    "pos": pos,
                    "pil_image": pil_img,
                })

    if total_images:
        log(f"{total_images} imagem(ns) encontrada(s), "
            f"{len(significant)} relevante(s) para recompactar.")
    return significant


def _encode_one(entry, quality, scale):
    pil_img = entry["pil_image"]
    if scale < 1.0:
        new_w = max(1, int(pil_img.width * scale))
        new_h = max(1, int(pil_img.height * scale))
        img = pil_img.resize((new_w, new_h), Image.LANCZOS)
    else:
        img = pil_img
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), img.width, img.height, img.mode


def _recompress_using_cache(input_path, output_path, image_cache, quality, scale, cancel_flag):
    """Reabre o PDF original (sempre a partir do arquivo original, nunca de
    uma versão já compactada, para não acumular perdas) e substitui só as
    imagens relevantes pelas versões recodificadas na qualidade/escala
    pedidas. A codificação de cada imagem roda em paralelo (usa vários
    núcleos do processador), já que é a parte mais pesada."""
    lookup = {(e["page_idx"], e["pos"]): e for e in image_cache}
    if not lookup:
        return None

    # Usa o pool COMPARTILHADO entre todos os arquivos (em vez de criar um
    # pool novo por chamada) — assim, mesmo compactando vários arquivos ao
    # mesmo tempo, o total de imagens sendo recodificadas simultaneamente
    # continua limitado a _MAX_WORKERS no programa inteiro.
    encoded = {}
    futures = {
        _IMAGE_EXECUTOR.submit(_encode_one, entry, quality, scale): key
        for key, entry in lookup.items()
    }
    for future in as_completed(futures):
        if cancel_flag and cancel_flag.is_set():
            # Cancela as tarefas desta chamada que ainda não começaram a
            # rodar (as que já começaram terminam normalmente em segundo
            # plano, mas o resultado delas é descartado).
            for fut in futures:
                fut.cancel()
            return None
        key = futures[future]
        try:
            encoded[key] = future.result()
        except Exception:
            continue

    if cancel_flag and cancel_flag.is_set():
        return None

    with pikepdf.open(input_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            for pos, (_name, raw_image) in enumerate(dict(page.images).items()):
                result = encoded.get((page_idx, pos))
                if result is None:
                    continue
                try:
                    jpeg_bytes, w, h, mode = result
                    raw_image.write(jpeg_bytes, filter=Name("/DCTDecode"))
                    raw_image.Width = w
                    raw_image.Height = h
                    raw_image.BitsPerComponent = 8
                    raw_image.ColorSpace = (
                        Name("/DeviceGray") if mode == "L" else Name("/DeviceRGB")
                    )
                    if "/SMask" in raw_image:
                        del raw_image.SMask
                    if "/Mask" in raw_image:
                        del raw_image.Mask
                except Exception:
                    continue

        pdf.save(output_path, compress_streams=True,
                 object_stream_mode=pikepdf.ObjectStreamMode.generate,
                 linearize=False)
    return get_size_mb(output_path)


def compress_pdf(input_path, output_path, target_mb=DEFAULT_TARGET_MB, log=print, cancel_flag=None):
    """
    Compacta um PDF tentando ficar abaixo de target_mb.
    Retorna (tamanho_final_mb, atingiu_meta: bool).
    """
    if pikepdf is None:
        raise RuntimeError(
            "A biblioteca pikepdf não está instalada. "
            "Rode o instalador (arquivo .bat) novamente."
        )

    original_size = get_size_mb(input_path)
    log(f"Tamanho original: {original_size:.2f} MB (alvo: {target_mb:.1f} MB)")

    # Usamos um arquivo temporário para todas as tentativas: assim funciona
    # mesmo quando output_path é igual a input_path (o pikepdf não permite
    # sobrescrever o arquivo que está aberto).
    tmp_path = output_path + ".tmp_compactando"

    try:
        # Passo 1: limpeza sem perdas (remove objetos duplicados, comprime streams)
        with pikepdf.open(input_path) as pdf:
            pdf.remove_unreferenced_resources()
            pdf.save(tmp_path, compress_streams=True,
                     object_stream_mode=pikepdf.ObjectStreamMode.generate,
                     linearize=False)
        size = get_size_mb(tmp_path)
        log(f"Após limpeza inicial: {size:.2f} MB")

        best_size = size
        with open(tmp_path, "rb") as f:
            best_bytes = f.read()

        if size <= target_mb:
            log("Meta atingida sem precisar reduzir qualidade das imagens.")
            os.replace(tmp_path, output_path)
            return size, True

        if cancel_flag and cancel_flag.is_set():
            os.replace(tmp_path, output_path)
            return size, size <= target_mb

        # Passo 2: decodifica as imagens relevantes UMA vez só (custo fixo)
        image_cache = _collect_significant_images(input_path, log)
        if not image_cache:
            log("Não há imagens grandes o suficiente para compactar mais. "
                "Este é o melhor resultado possível para este arquivo.")
            with open(output_path, "wb") as f:
                f.write(best_bytes)
            return best_size, best_size <= target_mb

        # Passo 3: tenta combinações de qualidade/escala em ordem crescente
        # de agressividade, começando de um ponto estimado a partir de
        # quanto ainda falta reduzir — evita testar tentativas fracas demais
        # quando já se sabe que vai precisar de muita compactação.
        ratio_needed = target_mb / size if size > 0 else 1.0
        start_idx = _pick_start_level(ratio_needed)
        levels = _COMPRESSION_LEVELS[start_idx:]

        for quality, scale in levels:
            if cancel_flag and cancel_flag.is_set():
                log("Cancelado pelo usuário.")
                break

            try:
                new_size = _recompress_using_cache(
                    input_path, tmp_path, image_cache, quality, scale, cancel_flag
                )
            except Exception as e:
                log(f"  Aviso: falha na tentativa qualidade={quality} escala={scale}: {e}")
                continue

            if new_size is None:
                break  # cancelado durante a codificação

            log(f"Tentativa (qualidade imagem={quality}%, escala={int(scale*100)}%): "
                f"{new_size:.2f} MB")

            if new_size < best_size:
                best_size = new_size
                with open(tmp_path, "rb") as f:
                    best_bytes = f.read()

            if new_size <= target_mb:
                log(f"Meta atingida: {new_size:.2f} MB <= {target_mb:.1f} MB")
                with open(output_path, "wb") as f:
                    f.write(best_bytes)
                return new_size, True

        # Não atingiu a meta dentro do limite seguro de compactação: grava a
        # melhor tentativa encontrada (a mais comprimida sem arriscar deixar
        # as imagens ilegíveis).
        with open(output_path, "wb") as f:
            f.write(best_bytes)
        final_size = get_size_mb(output_path)
        log(f"Não foi possível atingir {target_mb:.1f} MB mantendo as imagens "
            f"legíveis. Melhor resultado obtido: {final_size:.2f} MB.")
        return final_size, final_size <= target_mb
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _save_page_range(pdf, start, end, output_path):
    """Salva pdf.pages[start:end] como um PDF novo e independente. Não
    recompacta as imagens de novo — elas já vêm no nível final escolhido
    pela etapa de compactação, só reorganiza as páginas."""
    new_pdf = pikepdf.Pdf.new()
    try:
        new_pdf.pages.extend(pdf.pages[start:end])
        new_pdf.save(output_path, compress_streams=True,
                     object_stream_mode=pikepdf.ObjectStreamMode.generate,
                     linearize=False)
    finally:
        new_pdf.close()
    return get_size_mb(output_path)


def _split_pdf_to_target(compressed_path, target_mb, tmp_dir, cancel_flag):
    """Divide compressed_path (já compactado no nível máximo seguro) em
    quantas partes forem necessárias para cada uma ficar dentro de
    target_mb, dividindo por página ao meio recursivamente (busca binária)
    até cada parte caber no limite ou virar uma única página (piso — uma
    página não pode ser dividida mais).

    Retorna uma lista de tuplas (start, end, caminho_temporario, tamanho_mb)
    em ordem de página."""
    parts = []
    with pikepdf.open(compressed_path) as pdf:
        n_pages = len(pdf.pages)

        def recurse(start, end):
            if cancel_flag and cancel_flag.is_set():
                return
            tmp_path = os.path.join(tmp_dir, f"parte_{start}_{end}.pdf")
            size = _save_page_range(pdf, start, end, tmp_path)
            n = end - start
            if size <= target_mb or n <= 1:
                parts.append((start, end, tmp_path, size))
                return
            # Este pedaço ainda está grande demais e será subdividido — o
            # arquivo temporário dele não vai virar uma parte final, então
            # é removido para não sobrar lixo na pasta temporária.
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            mid = start + n // 2
            recurse(start, mid)
            recurse(mid, end)

        recurse(0, n_pages)

    parts.sort(key=lambda item: item[0])
    return parts


def compress_and_maybe_split(input_pdf_path, output_dir, base_name,
                              target_mb=DEFAULT_TARGET_MB, log=print, cancel_flag=None):
    """
    Compacta input_pdf_path no máximo de agressividade que ainda preserva a
    legibilidade das imagens. Se, mesmo assim, o resultado ficar acima de
    target_mb e o PDF tiver mais de uma página, divide o arquivo em quantas
    partes forem necessárias — cada uma dentro do limite (quando possível)
    — nomeadas "<base_name>_parte1_de_N.pdf", "_parte2_de_N.pdf", etc.

    Retorna uma lista de dicionários, um por arquivo final gerado, na
    ordem correta: [{"path": ..., "size_mb": ..., "ok": bool}, ...]
    """
    os.makedirs(output_dir, exist_ok=True)
    single_path = os.path.join(output_dir, base_name + ".pdf")

    size, ok = compress_pdf(input_pdf_path, single_path, target_mb=target_mb,
                             log=log, cancel_flag=cancel_flag)

    if ok or (cancel_flag and cancel_flag.is_set()):
        return [{"path": single_path, "size_mb": size, "ok": ok}]

    with pikepdf.open(single_path) as _pdf:
        n_pages = len(_pdf.pages)

    if n_pages <= 1:
        log("O arquivo tem só 1 página, então não pode ser dividido em "
            "partes menores — este é o melhor resultado possível.")
        return [{"path": single_path, "size_mb": size, "ok": False}]

    log(f"\nMesmo no nível máximo de compactação que preserva a "
        f"legibilidade, o arquivo ficou em {size:.2f} MB (acima de "
        f"{target_mb:.1f} MB). Dividindo em partes...")

    tmp_dir = tempfile.mkdtemp(prefix="pdfsplit_", dir=output_dir)
    try:
        parts = _split_pdf_to_target(single_path, target_mb, tmp_dir, cancel_flag)

        if cancel_flag and cancel_flag.is_set():
            # Cancelado no meio da divisão: as partes já geradas cobrem só
            # parte das páginas, então descartá-las e manter o arquivo
            # compactado (ainda inteiro) é mais seguro do que devolver um
            # resultado "dividido" faltando páginas.
            log("Cancelado pelo usuário durante a divisão em partes — "
                "mantendo o arquivo compactado (ainda não dividido).")
            for _start, _end, tmp_path, _part_size in parts:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            return [{"path": single_path, "size_mb": size, "ok": False}]

        total = len(parts)
        results = []
        for i, (start, end, tmp_path, part_size) in enumerate(parts, start=1):
            final_name = f"{base_name}_parte{i}_de_{total}.pdf"
            final_path = os.path.join(output_dir, final_name)
            os.replace(tmp_path, final_path)
            part_ok = part_size <= target_mb
            n_paginas = end - start
            if part_ok:
                log(f"  {final_name}: {part_size:.2f} MB ({n_paginas} página(s))")
            else:
                log(f"  ⚠ {final_name}: {part_size:.2f} MB ({n_paginas} página(s)) — "
                    f"uma única página já é maior que o limite sozinha, "
                    f"não deu para dividir mais sem cortar página ao meio.")
            results.append({"path": final_path, "size_mb": part_size, "ok": part_ok})

        if not results:
            # cancelado antes de gerar qualquer parte
            return [{"path": single_path, "size_mb": size, "ok": False}]

        if os.path.exists(single_path):
            os.remove(single_path)
        return results
    finally:
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


def image_to_pdf(image_path, output_dir, base_name, log=print, cancel_flag=None):
    """Converte uma única imagem em um arquivo PDF, direto — sem nenhuma
    etapa de compactação ou divisão. Fotos raramente ultrapassam o limite
    dos sistemas judiciais, então a aba "Fotos → PDF" não usa as opções de
    tamanho máximo/compactação da aba de PDFs; a conversão é sempre rápida
    e preserva a foto na qualidade original."""
    if Image is None:
        raise RuntimeError("A biblioteca Pillow não está instalada.")

    os.makedirs(output_dir, exist_ok=True)
    final_path = os.path.join(output_dir, base_name + ".pdf")

    img = Image.open(image_path)
    if img.mode in ("RGBA", "LA", "P"):
        # Compõe sobre um fundo branco antes de descartar o canal alfa —
        # um .convert("RGB") direto mantém o RGB "por baixo" da
        # transparência (geralmente preto), deixando o fundo da foto
        # preto em vez de branco no PDF final.
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.save(final_path, "PDF", resolution=150.0)
    size = get_size_mb(final_path)
    log(f"PDF gerado: {size:.2f} MB")
    return [{"path": final_path, "size_mb": size, "ok": True}]


def merge_pdfs(input_paths, output_path, log=print, cancel_flag=None, progress_callback=None):
    """
    Junta vários PDFs em um único arquivo, na ordem em que aparecem em
    input_paths: quando um termina, o próximo começa logo em seguida.
    Sempre gera exatamente 1 arquivo PDF final — nunca divide em partes.

    Retorna o tamanho final em MB, ou None se foi cancelado pelo usuário.
    Levanta uma exceção com uma mensagem clara se algum arquivo não puder
    ser aberto (a operação inteira é interrompida nesse caso, para não
    gerar um resultado juntado pela metade).
    """
    if pikepdf is None:
        raise RuntimeError(
            "A biblioteca pikepdf não está instalada. "
            "Rode o instalador (arquivo .bat) novamente."
        )
    if not input_paths:
        raise ValueError("Nenhum arquivo selecionado para juntar.")

    total = len(input_paths)
    # Usamos um arquivo temporário e só substituímos o destino no final: assim
    # funciona mesmo que o destino escolhido seja igual a um dos arquivos de
    # entrada (cada entrada já foi copiada para a memória antes de gravarmos).
    tmp_path = output_path + ".tmp_juntando"
    n_pages = 0
    merged = pikepdf.Pdf.new()
    try:
        for i, path in enumerate(input_paths, start=1):
            if cancel_flag and cancel_flag.is_set():
                log("Cancelado pelo usuário.")
                return None

            name = os.path.basename(path)
            log(f"Adicionando ({i}/{total}): {name}")
            try:
                with pikepdf.open(path) as src:
                    merged.pages.extend(src.pages)
            except Exception as e:
                raise RuntimeError(
                    f"Não foi possível abrir o arquivo '{name}'. Ele pode "
                    f"estar corrompido, protegido por senha, ou não ser um "
                    f"PDF válido.\n\nDetalhe: {e}"
                ) from e

            if progress_callback:
                progress_callback(i, total)

        if cancel_flag and cancel_flag.is_set():
            log("Cancelado pelo usuário.")
            return None

        n_pages = len(merged.pages)
        if n_pages == 0:
            raise RuntimeError("Os arquivos selecionados não têm nenhuma página.")

        merged.save(tmp_path, compress_streams=True,
                    object_stream_mode=pikepdf.ObjectStreamMode.generate,
                    linearize=False)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise
    finally:
        merged.close()

    os.replace(tmp_path, output_path)
    size = get_size_mb(output_path)
    log(f"PDF final: {size:.2f} MB, {n_pages} página(s).")
    return size


# --------------------------------------------------------------------------
# Interface gráfica
# --------------------------------------------------------------------------

class ScrollableLog(scrolledtext.ScrolledText):
    def log(self, message):
        self.configure(state="normal")
        self.insert("end", message + "\n")
        self.see("end")
        self.configure(state="disabled")
        self.update_idletasks()


class TargetSizeFrame(ttk.Frame):
    """Três opções de tamanho máximo: 12 MB, 5 MB, ou um valor customizado
    digitado pelo usuário."""

    def __init__(self, master):
        super().__init__(master)
        self.choice_var = tk.StringVar(value="12")
        self.custom_var = tk.StringVar(value="")

        self.radio_12 = ttk.Radiobutton(
            self, text="12 MB",
            variable=self.choice_var, value="12", command=self._on_choice_change,
        )
        self.radio_12.pack(anchor="w")

        self.radio_5 = ttk.Radiobutton(
            self, text="5 MB", variable=self.choice_var, value="5",
            command=self._on_choice_change,
        )
        self.radio_5.pack(anchor="w")

        custom_row = ttk.Frame(self)
        custom_row.pack(anchor="w", fill="x")
        self.radio_custom = ttk.Radiobutton(
            custom_row, text="Outro tamanho (MB):", variable=self.choice_var,
            value="custom", command=self._on_choice_change,
        )
        self.radio_custom.pack(side="left")
        self.custom_entry = ttk.Entry(custom_row, textvariable=self.custom_var,
                                       width=8, state="disabled")
        self.custom_entry.pack(side="left", padx=(4, 0))

        self._radio_buttons = (self.radio_12, self.radio_5, self.radio_custom)

    def _on_choice_change(self):
        if self.choice_var.get() == "custom":
            self.custom_entry.configure(state="normal")
            self.custom_entry.focus_set()
        else:
            self.custom_entry.configure(state="disabled")

    def set_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for radio in self._radio_buttons:
            radio.configure(state=state)
        if enabled:
            # Reaplica a regra "campo customizado só habilitado se selecionado"
            self._on_choice_change()
        else:
            self.custom_entry.configure(state="disabled")

    def get_target_mb(self):
        """Retorna o tamanho máximo escolhido em MB, ou levanta ValueError
        com uma mensagem amigável se o valor customizado for inválido."""
        choice = self.choice_var.get()
        if choice == "12":
            return 12.0
        if choice == "5":
            return 5.0

        raw = self.custom_var.get().strip().replace(",", ".")
        if not raw:
            raise ValueError("Digite o tamanho desejado em MB no campo \"Outro tamanho\".")
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"Tamanho inválido: \"{raw}\". Digite um número, ex: 8 ou 8.5.")
        if value <= 0:
            raise ValueError("O tamanho precisa ser maior que zero.")
        return value


class FileListFrame(ttk.Frame):
    """Frame com botão de seleção de arquivos + lista + botão remover."""

    def __init__(self, master, filetypes, add_label):
        super().__init__(master)
        self.filetypes = filetypes
        self.paths = []

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x")

        self.add_btn = ttk.Button(btn_frame, text=add_label, command=self.add_files)
        self.add_btn.pack(side="left")
        self.remove_btn = ttk.Button(btn_frame, text="Remover selecionado(s)",
                                      command=self.remove_selected)
        self.remove_btn.pack(side="left", padx=6)
        self.clear_btn = ttk.Button(btn_frame, text="Limpar lista",
                                     command=self.clear_all)
        self.clear_btn.pack(side="left")

        self.listbox = tk.Listbox(self, selectmode="extended", height=8)
        self.listbox.pack(fill="both", expand=True, pady=(6, 0))

    def add_files(self):
        files = filedialog.askopenfilenames(title="Selecionar arquivos",
                                             filetypes=self.filetypes)
        for f in files:
            if f not in self.paths:
                self.paths.append(f)
                self.listbox.insert("end", f)

    def remove_selected(self):
        selected = list(self.listbox.curselection())
        for index in reversed(selected):
            self.listbox.delete(index)
            del self.paths[index]

    def clear_all(self):
        self.listbox.delete(0, "end")
        self.paths = []

    def set_enabled(self, enabled):
        """Desabilita a seleção de arquivos enquanto um processamento está
        rodando, para deixar claro que o programa está ocupado (e evitar
        que a lista mude no meio do processamento)."""
        state = "normal" if enabled else "disabled"
        self.add_btn.configure(state=state)
        self.remove_btn.configure(state=state)
        self.clear_btn.configure(state=state)
        self.listbox.configure(state=state)


class CompactarTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=10)
        self.app = app
        self.cancel_flag = threading.Event()

        ttk.Label(self, text="1. Selecione os arquivos PDF que deseja compactar "
                              "(pode escolher vários — são compactados ao mesmo tempo):",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")

        self.file_list = FileListFrame(
            self, filetypes=[("Arquivos PDF", "*.pdf")], add_label="Adicionar PDF(s)...")
        self.file_list.pack(fill="both", expand=True, pady=(4, 10))

        ttk.Label(self, text="2. Escolha o tamanho máximo desejado:",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.target_frame = TargetSizeFrame(self)
        self.target_frame.pack(anchor="w", pady=(4, 10))

        ttk.Label(self, text="3. Salvar em:",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=(4, 10))

        self.output_mode = tk.StringVar(value="mesma_pasta")
        ttk.Radiobutton(opts, text="Mesma pasta do original (sufixo _compactado)",
                         variable=self.output_mode, value="mesma_pasta").pack(side="left", padx=(0, 10))
        ttk.Radiobutton(opts, text="Escolher pasta...",
                         variable=self.output_mode, value="escolher").pack(side="left")

        action_frame = ttk.Frame(self)
        action_frame.pack(fill="x", pady=(0, 6))
        self.process_btn = ttk.Button(action_frame, text="Compactar PDF(s)",
                                       command=self.start_processing)
        self.process_btn.pack(side="left")
        ttk.Button(action_frame, text="Cancelar",
                   command=self.cancel).pack(side="left", padx=6)

        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", pady=(0, 6))
        self.status_var = tk.StringVar(value="Pronto.")
        ttk.Label(status_frame, textvariable=self.status_var).pack(anchor="w")
        self.progress = ttk.Progressbar(status_frame, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(2, 0))

        ttk.Label(self, text="Progresso:", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.log_widget = ScrollableLog(self, height=12, state="disabled", wrap="word")
        self.log_widget.pack(fill="both", expand=True)

    def cancel(self):
        self.cancel_flag.set()

    def start_processing(self):
        if not self.file_list.paths:
            messagebox.showwarning(APP_TITLE, "Selecione pelo menos um arquivo PDF.")
            return
        try:
            target_mb = self.target_frame.get_target_mb()
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return

        output_dir = None
        if self.output_mode.get() == "escolher":
            output_dir = filedialog.askdirectory(title="Escolha a pasta de destino")
            if not output_dir:
                return

        self.cancel_flag.clear()
        self.process_btn.configure(state="disabled")
        self.file_list.set_enabled(False)
        self.target_frame.set_enabled(False)
        self.progress.configure(value=0)
        thread = threading.Thread(
            target=self._run, args=(list(self.file_list.paths), target_mb, output_dir),
            daemon=True,
        )
        thread.start()

    def _run(self, paths, target_mb, output_dir):
        total = len(paths)
        progress_lock = threading.Lock()
        completed = [0]

        def process_one(path):
            if self.cancel_flag.is_set():
                return
            name = os.path.basename(path)
            self._log(f"\n=== {name} ===")

            def log_for_file(message):
                self._log(f"[{name}] {message}")

            try:
                src = Path(path)
                dest_dir = Path(output_dir) if output_dir else src.parent
                base_name = f"{src.stem}_compactado"

                results = compress_and_maybe_split(
                    str(src), str(dest_dir), base_name, target_mb=target_mb,
                    log=log_for_file, cancel_flag=self.cancel_flag,
                )
                self._log_results(results, dest_dir)
            except Exception as e:
                self._log(f"✘ Erro ao processar {name}: {e}")
                traceback.print_exc()
            finally:
                with progress_lock:
                    completed[0] += 1
                    n = completed[0]
                self._set_status(f"Processando... {n} de {total} arquivo(s) concluído(s)")
                self._set_progress(int(n / total * 100))

        # Compacta vários arquivos ao mesmo tempo (em paralelo), até
        # _MAX_CONCURRENT_FILES por vez. O trabalho pesado de cada um usa o
        # pool de imagens compartilhado (_IMAGE_EXECUTOR), então isso não
        # sobrecarrega o processador além do necessário.
        max_workers = max(1, min(_MAX_CONCURRENT_FILES, total))
        with ThreadPoolExecutor(max_workers=max_workers) as file_executor:
            futures = [file_executor.submit(process_one, path) for path in paths]
            for future in futures:
                future.result()

        self._log("\nTudo concluído.")
        self._set_status("Concluído." if not self.cancel_flag.is_set() else "Cancelado.")
        self.after(0, self.process_btn.configure, {"state": "normal"})
        self.after(0, self.file_list.set_enabled, True)
        self.after(0, self.target_frame.set_enabled, True)

    def _log_results(self, results, dest_dir):
        if len(results) == 1:
            r = results[0]
            name = Path(r["path"]).name
            if r["ok"]:
                self._log(f"✔ Concluído: {name} ({r['size_mb']:.2f} MB) — salvo em: {dest_dir}")
            else:
                self._log(f"⚠ Concluído, mas acima do alvo: {name} "
                           f"({r['size_mb']:.2f} MB) — salvo em: {dest_dir}")
        else:
            all_ok = all(r["ok"] for r in results)
            icon = "✔" if all_ok else "⚠"
            self._log(f"{icon} Dividido em {len(results)} partes — salvo em: {dest_dir}")
            for r in results:
                marca = "" if r["ok"] else " (ainda acima do alvo)"
                self._log(f"   - {Path(r['path']).name}: {r['size_mb']:.2f} MB{marca}")

    def _log(self, message):
        self.after(0, self.log_widget.log, message)

    def _set_status(self, message):
        self.after(0, self.status_var.set, message)

    def _set_progress(self, value):
        self.after(0, self.progress.configure, {"value": value})


class FotosTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=10)
        self.app = app
        self.cancel_flag = threading.Event()

        ttk.Label(self, text="1. Selecione as fotos que deseja transformar em PDF "
                              "(cada foto vira um PDF separado; várias fotos são "
                              "processadas ao mesmo tempo):",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")

        filetypes = [("Imagens", " ".join(f"*{ext}" for ext in IMAGE_EXTENSIONS)),
                     ("Todos os arquivos", "*.*")]
        self.file_list = FileListFrame(self, filetypes=filetypes, add_label="Adicionar foto(s)...")
        self.file_list.pack(fill="both", expand=True, pady=(4, 10))

        ttk.Label(self, text="2. Salvar em:",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=(4, 10))

        self.output_mode = tk.StringVar(value="mesma_pasta")
        ttk.Radiobutton(opts, text="Mesma pasta da foto original",
                         variable=self.output_mode, value="mesma_pasta").pack(side="left", padx=(0, 10))
        ttk.Radiobutton(opts, text="Escolher pasta...",
                         variable=self.output_mode, value="escolher").pack(side="left")

        action_frame = ttk.Frame(self)
        action_frame.pack(fill="x", pady=(0, 6))
        self.process_btn = ttk.Button(action_frame, text="Converter em PDF",
                                       command=self.start_processing)
        self.process_btn.pack(side="left")
        ttk.Button(action_frame, text="Cancelar",
                   command=self.cancel).pack(side="left", padx=6)

        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", pady=(0, 6))
        self.status_var = tk.StringVar(value="Pronto.")
        ttk.Label(status_frame, textvariable=self.status_var).pack(anchor="w")
        self.progress = ttk.Progressbar(status_frame, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(2, 0))

        ttk.Label(self, text="Progresso:", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.log_widget = ScrollableLog(self, height=12, state="disabled", wrap="word")
        self.log_widget.pack(fill="both", expand=True)

    def cancel(self):
        self.cancel_flag.set()

    def start_processing(self):
        if not self.file_list.paths:
            messagebox.showwarning(APP_TITLE, "Selecione pelo menos uma foto.")
            return

        output_dir = None
        if self.output_mode.get() == "escolher":
            output_dir = filedialog.askdirectory(title="Escolha a pasta de destino")
            if not output_dir:
                return

        self.cancel_flag.clear()
        self.process_btn.configure(state="disabled")
        self.file_list.set_enabled(False)
        self.progress.configure(value=0)
        thread = threading.Thread(
            target=self._run, args=(list(self.file_list.paths), output_dir),
            daemon=True,
        )
        thread.start()

    def _run(self, paths, output_dir):
        total = len(paths)
        progress_lock = threading.Lock()
        completed = [0]

        def process_one(path):
            if self.cancel_flag.is_set():
                return
            name = os.path.basename(path)
            self._log(f"\n=== {name} ===")

            def log_for_file(message):
                self._log(f"[{name}] {message}")

            try:
                src = Path(path)
                dest_dir = Path(output_dir) if output_dir else src.parent

                results = image_to_pdf(
                    str(src), str(dest_dir), src.stem,
                    log=log_for_file, cancel_flag=self.cancel_flag,
                )
                self._log_results(results, dest_dir)
            except Exception as e:
                self._log(f"✘ Erro ao processar {name}: {e}")
                traceback.print_exc()
            finally:
                with progress_lock:
                    completed[0] += 1
                    n = completed[0]
                self._set_status(f"Processando... {n} de {total} arquivo(s) concluído(s)")
                self._set_progress(int(n / total * 100))

        # Converte várias fotos ao mesmo tempo (em paralelo), até
        # _MAX_CONCURRENT_FILES por vez — mesmo esquema usado na aba de
        # compactar PDFs.
        max_workers = max(1, min(_MAX_CONCURRENT_FILES, total))
        with ThreadPoolExecutor(max_workers=max_workers) as file_executor:
            futures = [file_executor.submit(process_one, path) for path in paths]
            for future in futures:
                future.result()

        self._log("\nTudo concluído.")
        self._set_status("Concluído." if not self.cancel_flag.is_set() else "Cancelado.")
        self.after(0, self.process_btn.configure, {"state": "normal"})
        self.after(0, self.file_list.set_enabled, True)

    def _log_results(self, results, dest_dir):
        if len(results) == 1:
            r = results[0]
            name = Path(r["path"]).name
            if r["ok"]:
                self._log(f"✔ Concluído: {name} ({r['size_mb']:.2f} MB) — salvo em: {dest_dir}")
            else:
                self._log(f"⚠ Concluído, mas acima do alvo: {name} "
                           f"({r['size_mb']:.2f} MB) — salvo em: {dest_dir}")
        else:
            all_ok = all(r["ok"] for r in results)
            icon = "✔" if all_ok else "⚠"
            self._log(f"{icon} Dividido em {len(results)} partes — salvo em: {dest_dir}")
            for r in results:
                marca = "" if r["ok"] else " (ainda acima do alvo)"
                self._log(f"   - {Path(r['path']).name}: {r['size_mb']:.2f} MB{marca}")

    def _log(self, message):
        self.after(0, self.log_widget.log, message)

    def _set_status(self, message):
        self.after(0, self.status_var.set, message)

    def _set_progress(self, value):
        self.after(0, self.progress.configure, {"value": value})


class MergeFileListFrame(ttk.Frame):
    """Frame com lista de PDFs numerada (ordem = ordem final no PDF juntado),
    com botões para adicionar, remover, limpar e reordenar (mover para cima
    / para baixo)."""

    def __init__(self, master):
        super().__init__(master)
        self.paths = []

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x")

        self.add_btn = ttk.Button(btn_frame, text="Adicionar PDF(s)...",
                                   command=self.add_files)
        self.add_btn.pack(side="left")
        self.remove_btn = ttk.Button(btn_frame, text="Remover selecionado(s)",
                                      command=self.remove_selected)
        self.remove_btn.pack(side="left", padx=6)
        self.clear_btn = ttk.Button(btn_frame, text="Limpar lista",
                                     command=self.clear_all)
        self.clear_btn.pack(side="left", padx=(0, 6))
        self.up_btn = ttk.Button(btn_frame, text="Mover para cima",
                                  command=self.move_up)
        self.up_btn.pack(side="left", padx=(0, 6))
        self.down_btn = ttk.Button(btn_frame, text="Mover para baixo",
                                    command=self.move_down)
        self.down_btn.pack(side="left")

        self.listbox = tk.Listbox(self, selectmode="extended", height=8)
        self.listbox.pack(fill="both", expand=True, pady=(6, 0))

    def _refresh_display(self):
        selected = list(self.listbox.curselection())
        self.listbox.delete(0, "end")
        for i, path in enumerate(self.paths, start=1):
            self.listbox.insert("end", f"{i}. {os.path.basename(path)}")
        for index in selected:
            if 0 <= index < len(self.paths):
                self.listbox.selection_set(index)

    def add_files(self):
        files = filedialog.askopenfilenames(title="Selecionar arquivos PDF",
                                             filetypes=[("Arquivos PDF", "*.pdf")])
        for f in files:
            if f not in self.paths:
                self.paths.append(f)
        self._refresh_display()

    def remove_selected(self):
        selected = list(self.listbox.curselection())
        for index in reversed(selected):
            del self.paths[index]
        self._refresh_display()

    def clear_all(self):
        self.paths = []
        self._refresh_display()

    def move_up(self):
        selected = list(self.listbox.curselection())
        if not selected:
            return
        for index in selected:
            if index == 0:
                continue
            self.paths[index - 1], self.paths[index] = self.paths[index], self.paths[index - 1]
        self._refresh_display()
        for index in selected:
            self.listbox.selection_set(max(0, index - 1))

    def move_down(self):
        selected = list(self.listbox.curselection())
        if not selected:
            return
        n = len(self.paths)
        for index in reversed(selected):
            if index >= n - 1:
                continue
            self.paths[index + 1], self.paths[index] = self.paths[index], self.paths[index + 1]
        self._refresh_display()
        for index in selected:
            self.listbox.selection_set(min(n - 1, index + 1))

    def set_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.add_btn.configure(state=state)
        self.remove_btn.configure(state=state)
        self.clear_btn.configure(state=state)
        self.up_btn.configure(state=state)
        self.down_btn.configure(state=state)
        self.listbox.configure(state=state)


class JuntarTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=10)
        self.app = app
        self.cancel_flag = threading.Event()

        ttk.Label(self, text="1. Selecione os PDFs que deseja juntar e coloque na ordem "
                              "desejada (use \"Mover para cima/baixo\" para reordenar — "
                              "quando um PDF terminar, o próximo da lista começa em seguida):",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")

        self.file_list = MergeFileListFrame(self)
        self.file_list.pack(fill="both", expand=True, pady=(4, 10))

        ttk.Label(self, text="2. Salvar o PDF juntado como:",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=(4, 10))
        self.output_path_var = tk.StringVar(value="")
        self.output_entry = ttk.Entry(opts, textvariable=self.output_path_var,
                                       state="readonly", width=60)
        self.output_entry.pack(side="left", fill="x", expand=True)
        self.choose_output_btn = ttk.Button(opts, text="Escolher onde salvar...",
                                             command=self.choose_output)
        self.choose_output_btn.pack(side="left", padx=(6, 0))

        action_frame = ttk.Frame(self)
        action_frame.pack(fill="x", pady=(0, 6))
        self.process_btn = ttk.Button(action_frame, text="Juntar PDFs",
                                       command=self.start_processing)
        self.process_btn.pack(side="left")
        ttk.Button(action_frame, text="Cancelar",
                   command=self.cancel).pack(side="left", padx=6)

        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", pady=(0, 6))
        self.status_var = tk.StringVar(value="Pronto.")
        ttk.Label(status_frame, textvariable=self.status_var).pack(anchor="w")
        self.progress = ttk.Progressbar(status_frame, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(2, 0))

        ttk.Label(self, text="Progresso:", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.log_widget = ScrollableLog(self, height=12, state="disabled", wrap="word")
        self.log_widget.pack(fill="both", expand=True)

    def cancel(self):
        self.cancel_flag.set()

    def choose_output(self):
        path = filedialog.asksaveasfilename(
            title="Salvar PDF juntado como",
            defaultextension=".pdf",
            filetypes=[("Arquivo PDF", "*.pdf")],
            initialfile="juntado.pdf",
        )
        if path:
            self.output_path_var.set(path)

    def start_processing(self):
        paths = list(self.file_list.paths)
        if len(paths) < 2:
            messagebox.showwarning(APP_TITLE,
                                    "Selecione pelo menos 2 arquivos PDF para juntar.")
            return
        output_path = self.output_path_var.get()
        if not output_path:
            messagebox.showwarning(APP_TITLE, "Escolha onde salvar o PDF final.")
            return

        self.cancel_flag.clear()
        self.process_btn.configure(state="disabled")
        self.file_list.set_enabled(False)
        self.choose_output_btn.configure(state="disabled")
        self.progress.configure(value=0)
        thread = threading.Thread(
            target=self._run, args=(paths, output_path),
            daemon=True,
        )
        thread.start()

    def _run(self, paths, output_path):
        self._set_status("Juntando arquivos...")
        self._set_progress(0)

        def on_progress(i, total_files):
            self._set_progress(int(i / total_files * 100))

        try:
            size = merge_pdfs(paths, output_path, log=self._log,
                               cancel_flag=self.cancel_flag,
                               progress_callback=on_progress)
            if size is None:
                self._log("Cancelado pelo usuário.")
                self._set_status("Cancelado.")
            else:
                name = Path(output_path).name
                self._set_progress(100)
                self._log(f"\n✔ Concluído: {name} ({size:.2f} MB, {len(paths)} arquivo(s) "
                           f"juntados) — salvo em: {Path(output_path).parent}")
                self._set_status("Concluído.")
        except Exception as e:
            self._log(f"✘ Erro ao juntar os arquivos: {e}")
            traceback.print_exc()
            self._set_status("Erro.")
        finally:
            self.after(0, self.process_btn.configure, {"state": "normal"})
            self.after(0, self.file_list.set_enabled, True)
            self.after(0, self.choose_output_btn.configure, {"state": "normal"})

    def _log(self, message):
        self.after(0, self.log_widget.log, message)

    def _set_status(self, message):
        self.after(0, self.status_var.set, message)

    def _set_progress(self, value):
        self.after(0, self.progress.configure, {"value": value})


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("760x620")
        self.minsize(640, 520)

        missing = []
        if pikepdf is None:
            missing.append("pikepdf")
        if Image is None:
            missing.append("Pillow")
        if missing:
            messagebox.showerror(
                APP_TITLE,
                "Faltam componentes necessários: " + ", ".join(missing) +
                "\n\nFeche o programa e rode novamente o arquivo "
                "'Compactador de PDF.bat' para instalar tudo automaticamente."
            )

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.compactar_tab = CompactarTab(notebook, self)
        self.fotos_tab = FotosTab(notebook, self)
        self.juntar_tab = JuntarTab(notebook, self)

        notebook.add(self.compactar_tab, text="Compactar PDF")
        notebook.add(self.fotos_tab, text="Fotos → PDF")
        notebook.add(self.juntar_tab, text="Juntar PDF")

        footer = ttk.Label(
            self,
            text="Dica: a compactação nunca deixa as imagens ilegíveis. Se um PDF não "
                 "couber no tamanho escolhido mesmo assim, ele é dividido automaticamente "
                 "em partes numeradas (parte1_de_N, parte2_de_N...), cada uma dentro do limite. "
                 "Na aba \"Juntar PDF\", os arquivos são sempre combinados em um único PDF "
                 f"final, na ordem que você escolher.  (versão {APP_VERSION})",
            foreground="#555555", wraplength=720, justify="left",
        )
        footer.pack(fill="x", padx=10, pady=(0, 8))

        # Checa por atualização em segundo plano, um pouco depois da janela
        # abrir (não atrasa a abertura nem trava se não tiver internet).
        self.after(1500, lambda: check_for_update_in_background(self._on_update_found))

    def _on_update_found(self, new_version, asset_url):
        # check_for_update_in_background roda em outra thread; Tkinter só
        # pode ser mexido a partir da thread principal, daí o self.after.
        self.after(0, self._prompt_update, new_version, asset_url)

    def _prompt_update(self, new_version, asset_url):
        quer_atualizar = messagebox.askyesno(
            APP_TITLE,
            f"Uma nova versão está disponível (v{new_version}, você está usando a "
            f"v{APP_VERSION}).\n\n"
            "Deseja baixar e instalar agora? O programa vai fechar e abrir de novo "
            "sozinho, já na versão nova.",
        )
        if not quer_atualizar:
            return
        try:
            download_and_apply_update(asset_url)
        except Exception as e:
            messagebox.showerror(
                APP_TITLE,
                "Não foi possível baixar a atualização agora. Você pode continuar "
                "usando esta versão normalmente e tentar de novo mais tarde "
                f"(o programa checa automaticamente sempre que abre).\n\nDetalhe: {e}",
            )
            return
        self.destroy()
        os._exit(0)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _fatal_error(
            "Compactador de PDF - erro ao iniciar",
            "Ocorreu um erro inesperado e o programa precisou fechar.\n\n"
            "Se isso continuar acontecendo, envie o arquivo 'erro.log' "
            "(salvo nesta mesma pasta) para quem te ajudou a configurar "
            "o programa.",
            details=traceback.format_exc(),
        )
        sys.exit(1)
