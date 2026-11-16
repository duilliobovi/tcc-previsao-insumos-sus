"""Cadeia de resolvedores, da estratégia mais específica para a mais genérica."""

from coletor.resolvedores.apis import ResolvedorInfoDengue, ResolvedorSidra, ResolvedorSocrata
from coletor.resolvedores.base import Contexto, Resolvedor
from coletor.resolvedores.ckan import ResolvedorCkan
from coletor.resolvedores.diretos import ResolvedorArquivoDireto
from coletor.resolvedores.ftp_datasus import ResolvedorFtpDatasus
from coletor.resolvedores.plone import ResolvedorPlone
from coletor.resolvedores.referencias import ResolvedorReferencia, ResolvedorSemEstrategia
from coletor.resolvedores.tabnet import ResolvedorTabnet
from coletor.resolvedores.zenodo import ResolvedorZenodo

# A ordem importa: referências conhecidas saem primeiro, para não serem
# raspadas por engano; ArquivoDireto vem antes de Plone, porque um PDF no
# gov.br é arquivo, não pasta; SemEstrategia fecha a cadeia e aceita tudo.
_CADEIA: tuple[type[Resolvedor], ...] = (
    ResolvedorReferencia,
    ResolvedorFtpDatasus,
    ResolvedorTabnet,
    ResolvedorSidra,
    ResolvedorInfoDengue,
    ResolvedorSocrata,
    ResolvedorZenodo,
    ResolvedorCkan,
    ResolvedorArquivoDireto,
    ResolvedorPlone,
    ResolvedorSemEstrategia,
)


def criar_resolvedores(contexto: Contexto) -> tuple[Resolvedor, ...]:
    return tuple(classe(contexto) for classe in _CADEIA)


__all__ = ["Contexto", "Resolvedor", "criar_resolvedores"]
