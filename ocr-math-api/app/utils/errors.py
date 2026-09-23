"""
errors.py
Assainissement des erreurs *inattendues* renvoyées au client.

Convention : une exception non prévue ne doit jamais voir son `str(exc)` brut
renvoyé dans le corps de la réponse HTTP —
il peut contenir des chemins de fichiers, le schéma/chemin de la base SQLite,
des bouts de l'entrée fournie ou des internes de librairies (PyMuPDF, PIL,
WeasyPrint...), ce qui offre gratuitement une carte des internes à quiconque
sonde l'API. La trace complète est loguée côté serveur ; le client ne reçoit
qu'un message générique, accompagné d'un identifiant court qu'il peut citer
dans un rapport de bug pour qu'on retrouve la trace correspondante (`grep`).
"""

import logging
import uuid


def log_unexpected(logger: logging.Logger, context: str, public_message: str) -> str:
    """
    À appeler DANS un bloc `except` : logue l'exception courante (trace
    complète, via `logger.exception`) avec un identifiant court, et retourne
    `public_message` suffixé de ce même identifiant — à passer tel quel en
    `detail` d'une `HTTPException`. Ne renvoie jamais le détail de l'exception.
    """
    error_id = uuid.uuid4().hex[:8]
    logger.exception("%s [ref=%s]", context, error_id)
    return f"{public_message} (référence : {error_id})"
