"""Funções puras (sem efeitos colaterais) compartilhadas pelos módulos."""

import re
import unicodedata
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from coletor.erros import ErroColeta

# Caracteres proibidos ou perigosos em nomes de arquivo (ext4 aceita quase
# tudo, mas manter o conjunto do Windows facilita copiar os dados depois).
_RE_CARACTERES_INVALIDOS = re.compile(r'[<>:"|?*\\\x00-\x1f]')


def slug(texto: str) -> str:
    """Converte um texto em identificador ASCII: ``"Ano/mês"`` → ``"ano_mes"``."""
    ascii_ = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_.lower()).strip("_")


def nome_de_url(url: str) -> str:
    """Último segmento do caminho da URL, já decodificado (``%20`` → espaço)."""
    return unquote(PurePosixPath(urlsplit(url).path).name)


def extensao_de_url(url: str) -> str:
    """Extensão do arquivo apontado pela URL, minúscula e sem o ponto."""
    return PurePosixPath(urlsplit(url).path).suffix.lower().lstrip(".")


def nome_seguro(texto: str) -> str:
    """Sanitiza um nome de arquivo vindo da web.

    Descarta diretórios (impede *path traversal* como ``../../etc/passwd``) e
    caracteres de controle. Levanta :class:`ErroColeta` se nada sobrar.
    """
    nome = PurePosixPath(texto.strip().replace("\\", "/")).name
    nome = _RE_CARACTERES_INVALIDOS.sub("_", nome).strip(" .")
    if not nome:
        raise ErroColeta(f"nome de arquivo inválido: {texto!r}")
    return nome


def formatar_bytes(tamanho: int | None) -> str:
    """Formata bytes em unidade legível (``1536`` → ``"1.5 KB"``)."""
    if tamanho is None:
        return "?"
    valor = float(tamanho)
    for unidade in ("B", "KB", "MB", "GB"):
        if valor < 1024:
            return f"{valor:.0f} B" if unidade == "B" else f"{valor:.1f} {unidade}"
        valor /= 1024
    return f"{valor:.1f} TB"
