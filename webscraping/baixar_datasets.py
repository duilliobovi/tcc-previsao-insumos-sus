#!/usr/bin/env python3
"""Ponto de entrada: ``python3 webscraping/baixar_datasets.py [opções]``.

Use ``--help`` para ver as opções e ``--simular`` para conferir o plano
(arquivos e volume) antes de baixar.
"""

from coletor.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
