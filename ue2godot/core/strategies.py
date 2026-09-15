# -*- coding: utf-8 -*-
"""
Chaînes de stratégies — le socle des replis.

POURQUOI CE MODULE PLUTÔT QUE DES `try/except` EN CASCADE
----------------------------------------------------------
Un repli écrit à la main dans un `except` a trois défauts qui se paient
plus tard :

  1. **Il est invisible.** Rien ne distingue « ça a marché du premier
     coup » de « ça a marché au troisième essai, en mode dégradé ». Or
     c'est exactement l'information qu'on cherche quand un résultat est
     bizarre. Ici, la stratégie retenue est TOUJOURS enregistrée et
     remonte dans le rapport d'étape.

  2. **Il masque les causes.** Un `except Exception: pass` enchaîné perd
     la raison de chaque échec. Ici, chaque tentative ratée conserve son
     message ; si toutes échouent, on rend les N raisons, pas la dernière.

  3. **Il dérive.** Sans structure, un repli finit par être ajouté « au
     cas où » sans qu'on vérifie qu'il produit un résultat juste. La
     signature impose donc un `validate` : un repli dont la sortie ne
     passe pas la validation est un ÉCHEC, pas un succès dégradé.

RÈGLE DE CONCEPTION, valable pour toute stratégie ajoutée ici
--------------------------------------------------------------
Une stratégie de repli doit atteindre le même résultat PAR UN AUTRE
CHEMIN. Elle ne doit jamais fabriquer une valeur vraisemblable quand
elle ne sait pas. S'il n'existe pas d'autre chemin correct, il ne faut
pas inventer de repli : un échec net est un meilleur résultat qu'une
donnée fausse qui traversera tout le pipeline sans être remarquée.

C'est la raison pour laquelle, par exemple, il n'y a PAS de repli
« commandlet headless » pour le scan Unreal : il produirait un manifeste
crédible mais amputé (World Partition ne chargerait pas les mêmes
cellules), et personne ne verrait la différence avant d'ouvrir la scène.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class Strategy:
    """Une manière d'obtenir le résultat.

    `name` sert au rapport ; `quality` dit ce qu'on perd en l'utilisant —
    "full" = équivalent au principal, "reduced" = résultat correct mais
    incomplet (à signaler à l'utilisateur), jamais "approximate" sans que
    la perte soit décrite dans `caveat`.
    """
    name: str
    run: Callable[..., Any]
    quality: str = "full"
    caveat: str = ""
    available: Optional[Callable[[], bool]] = None


@dataclass
class StrategyOutcome:
    value: Any = None
    strategy: str = ""
    quality: str = "full"
    caveat: str = ""
    attempts: List[Dict[str, str]] = field(default_factory=list)
    succeeded: bool = False

    def failure_summary(self) -> str:
        if self.succeeded:
            return ""
        if not self.attempts:
            return "Aucune stratégie disponible."
        return " | ".join(
            f"{a['strategy']}: {a['reason']}" for a in self.attempts
        )

    def used_fallback(self) -> bool:
        """Vrai si la stratégie retenue n'était pas la première.

        C'est ce que le rapport doit remonter : un pipeline qui marche
        uniquement grâce à ses replis est un pipeline qui va casser.
        """
        return self.succeeded and len(self.attempts) > 0


class StrategyChain:
    """Exécute des stratégies dans l'ordre jusqu'à la première qui réussit.

    `validate(value) -> (bool, reason)` est obligatoire : sans lui, une
    stratégie qui renvoie `None`, un fichier vide ou un objet tronqué
    serait comptée comme un succès — c'est précisément ainsi qu'un export
    GLB de 0 octet avait été compté comme réussi dans l'histoire de ce
    projet.
    """

    def __init__(self, label: str, strategies: List[Strategy],
                 validate: Callable[[Any], Tuple[bool, str]]):
        self.label = label
        self.strategies = strategies
        self.validate = validate

    def run(self, *args, **kwargs) -> StrategyOutcome:
        outcome = StrategyOutcome()

        for strategy in self.strategies:
            if strategy.available is not None:
                try:
                    if not strategy.available():
                        outcome.attempts.append({
                            "strategy": strategy.name,
                            "reason": "indisponible dans cet environnement",
                        })
                        continue
                except Exception as exc:
                    outcome.attempts.append({
                        "strategy": strategy.name,
                        "reason": f"test de disponibilité en échec : {exc}",
                    })
                    continue

            try:
                value = strategy.run(*args, **kwargs)
            except Exception as exc:
                outcome.attempts.append({
                    "strategy": strategy.name,
                    "reason": f"exception : {exc}",
                })
                continue

            try:
                is_valid, reason = self.validate(value)
            except Exception as exc:
                outcome.attempts.append({
                    "strategy": strategy.name,
                    "reason": f"validation impossible : {exc}",
                })
                continue

            if not is_valid:
                outcome.attempts.append({
                    "strategy": strategy.name,
                    "reason": reason or "résultat invalide",
                })
                continue

            outcome.value = value
            outcome.strategy = strategy.name
            outcome.quality = strategy.quality
            outcome.caveat = strategy.caveat
            outcome.succeeded = True
            return outcome

        return outcome


def summarize_outcomes(outcomes: Dict[str, StrategyOutcome]) -> Dict[str, Any]:
    """Bilan agrégé destiné au rapport d'étape.

    Deux chiffres comptent : combien d'éléments ont eu besoin d'un repli,
    et lesquels. Un taux de repli élevé signifie que la voie principale
    ne fonctionne plus dans cet environnement — un signal à traiter, pas
    un détail à ignorer sous prétexte que « ça passe quand même ».
    """
    by_strategy: Dict[str, int] = {}
    degraded: List[str] = []
    fallback_count = 0

    for key, outcome in outcomes.items():
        if not outcome.succeeded:
            continue
        by_strategy[outcome.strategy] = by_strategy.get(outcome.strategy, 0) + 1
        if outcome.used_fallback():
            fallback_count += 1
        if outcome.quality != "full":
            degraded.append(f"{key} ({outcome.quality}: {outcome.caveat})")

    return {
        "by_strategy": by_strategy,
        "fallback_count": fallback_count,
        "degraded": degraded[:50],
        "degraded_total": len(degraded),
    }
