# -*- coding: utf-8 -*-
"""
Transport vers l'éditeur Unreal — deux modes, dans cet ordre :

  (a) exécution distante automatique, via le protocole Python remote
      execution d'Unreal (ue2godot.orchestrator.remote_execution) ;
  (b) repli copier-coller : on génère le snippet à coller dans la console
      Python de l'éditeur.

Le repli (b) n'est PAS un vestige : c'est le mode confirmé fonctionnel en
test réel (preprocess + manifest exécutés avec succès sur la map `m1`), et
il reste la garantie que le pipeline est utilisable même si (a) échoue —
pare-feu, version d'Unreal différente, protocole modifié. L'architecture le
décrit comme « repli permanent, à exister dès le premier jour ». Ne le
supprimez pas au motif que (a) marche sur une machine donnée.
"""

import os
from typing import Optional, Tuple, List

from ue2godot.core.config import ResolvedConfig
from ue2godot.orchestrator.remote_execution import (
    RemoteExecutionClient, RemoteExecutionError, RemoteNode,
    CommandResult, MODE_EXEC_STATEMENT,
)


class UERemoteAdapter:
    def __init__(self, cfg: ResolvedConfig, default_engine_ini: str = ""):
        self.cfg = cfg
        # Permet de respecter les réglages multicast déclarés par le projet
        # plutôt que les seuls défauts en dur — un projet qui surcharge
        # RemoteExecutionMulticastGroupEndpoint serait sinon introuvable.
        self.default_engine_ini = default_engine_ini

    # ------------------------------------------------------------------
    # Construction de commande (commune aux deux modes)
    # ------------------------------------------------------------------

    def build_command(self, step_name: str, run_config_path: str,
                      module_root: Optional[str] = None) -> str:
        """Construit le snippet exécuté côté Unreal. Pure, sans effet de bord.

        `module_root` est le dossier contenant le paquet `ue2godot` vu depuis
        le process de l'UI. L'interpréteur embarqué d'Unreal a son propre
        sys.path, qui n'hérite de rien — sans cette insertion explicite,
        `import ue2godot...` y lève ModuleNotFoundError alors que le même
        import fonctionne parfaitement côté UI (bug rencontré en test réel).
        """
        path_prefix = f'import sys; sys.path.insert(0, r"{module_root}"); ' if module_root else ""
        return (
            f'{path_prefix}'
            f'import ue2godot.ue.entry as e; e.run_step("{step_name}", r"{run_config_path}")'
        )

    # ------------------------------------------------------------------
    # Mode (a) — exécution distante automatique
    # ------------------------------------------------------------------

    def can_execute_remotely(self, timeout: float = 2.0) -> bool:
        try:
            with RemoteExecutionClient.from_project(self.default_engine_ini) as client:
                return bool(client.discover_nodes(timeout=timeout))
        except RemoteExecutionError:
            return False

    def execute_steps_remotely(
        self,
        step_names: List[str],
        run_config_path: str,
        module_root: Optional[str] = None,
        node: Optional[RemoteNode] = None,
        discovery_timeout: float = 3.0,
        command_timeout: float = 3600.0,
        progress_callback=None,
    ) -> List[Tuple[str, bool, str]]:
        """Exécute les étapes dans l'éditeur, séquentiellement, sur UN SEUL
        canal ouvert pour toute la série.

        Retourne [(step_name, success, message)] dans l'ordre d'exécution.
        S'arrête à la première étape en échec : les étapes suivantes lisent
        les fichiers produits par les précédentes (le manifeste conditionne
        meshes, qui conditionne landscape…), donc continuer après un échec
        produirait des erreurs en cascade qui masqueraient la cause réelle.

        `command_timeout` est large par défaut : un scan de manifeste sur une
        grosse map dure plusieurs minutes, et un timeout court produirait un
        faux échec pendant qu'Unreal travaille encore.

        Lève RemoteExecutionError si le canal ne peut pas être établi — c'est
        à l'appelant de décider de basculer sur le repli copier-coller.
        """
        results: List[Tuple[str, bool, str]] = []

        with RemoteExecutionClient.from_project(self.default_engine_ini) as client:
            nodes = [node] if node else client.discover_nodes(timeout=discovery_timeout)
            if not nodes:
                raise RemoteExecutionError(
                    "Aucun éditeur Unreal joignable. Vérifiez qu'il est lancé et que "
                    "l'exécution distante est activée dans Project Settings > Plugins > Python."
                )

            target = nodes[0]
            client.open_command_channel(target, timeout=discovery_timeout + 5.0)

            for step_name in step_names:
                if progress_callback is not None:
                    progress_callback(step_name, "running")

                command = self.build_command(step_name, run_config_path, module_root=module_root)
                try:
                    result: CommandResult = client.run_command(
                        command, exec_mode=MODE_EXEC_STATEMENT, timeout=command_timeout,
                    )
                except RemoteExecutionError as exc:
                    results.append((step_name, False, str(exc)))
                    if progress_callback is not None:
                        progress_callback(step_name, "failed")
                    break

                message = result.output_text() or result.result
                results.append((step_name, result.success, message))
                if progress_callback is not None:
                    progress_callback(step_name, "ok" if result.success else "failed")

                if not result.success:
                    break

        return results

    # ------------------------------------------------------------------
    # Mode (b) — repli copier-coller
    # ------------------------------------------------------------------

    def build_fallback_instructions(
        self, step_names: List[str], run_config_path: str,
        module_root: Optional[str] = None, report_dir: str = "",
    ) -> List[str]:
        """Texte complet à afficher quand (a) n'est pas disponible."""
        commands = [
            self.build_command(name, run_config_path, module_root=module_root)
            for name in step_names
        ]
        lines = [
            "Exécution distante indisponible — repli manuel.",
            "",
            "Ces étapes font `import unreal` pour de vrai et doivent donc s'exécuter "
            "dans la console Python de l'éditeur Unreal, pas ici :",
            "",
            "1. Ouvrez ce projet dans Unreal Editor.",
            "2. Ouvrez la console Python : Window > Developer Tools > Python Console, "
            "ou la barre de commande en bas de l'éditeur (touche ~) en préfixant "
            "chaque ligne par `py `.",
            "3. Collez et exécutez, DANS L'ORDRE, une commande à la fois "
            "(si l'une échoue, inutile de continuer avec les suivantes) :",
            "",
        ] + commands + [""]

        lines.append(f"Config utilisée : {run_config_path}")
        if report_dir:
            lines.append(f"Rapports attendus dans : {report_dir}")
        lines += [
            "",
            "4. Revenez ici et relancez : les rapports seront relus et la "
            "vérification croisée sera lancée.",
        ]
        return lines

    def execute_step(self, step_name: str, run_config_path: str,
                     module_root: Optional[str] = None) -> bool:
        """Conservée pour compatibilité ascendante uniquement. N'écrit plus
        sur stdout : ce print partait dans le terminal du process UI (un
        terminal VS Code, typiquement), qui n'a rien à voir avec ce que reçoit
        Unreal — source de confusion réelle en test."""
        self.build_command(step_name, run_config_path, module_root=module_root)
        return True
