# -*- coding: utf-8 -*-
"""
Client du protocole d'exécution Python distante d'Unreal Engine.

CONTEXTE — pourquoi ce module existe
------------------------------------
Les étapes 0-4 font `import unreal` pour de vrai. Ce module n'existe QUE dans
l'interpréteur Python embarqué par l'éditeur Unreal, jamais dans le Python
standalone qui fait tourner l'UI PySide6. Les appeler directement depuis le
process de l'UI ne peut donc jamais fonctionner (c'est le
`AttributeError: module 'unreal' has no attribute 'get_editor_subsystem'`
rencontré en test réel : un paquet PyPI homonyme est importé à la place).

Le transport prévu par l'architecture (§14.3, transport (a)) est l'exécution
distante : l'éditeur Unreal ouvre un canal, un process externe lui envoie une
commande, et récupère stdout/stderr/succès. Ce fichier implémente ce canal.
Le repli copier-coller (transport (b)) reste en place et n'est PAS supprimé —
voir UERemoteAdapter.

PROTOCOLE (tel que documenté par Epic pour PythonScriptPlugin)
--------------------------------------------------------------
Toutes les enveloppes sont du JSON UTF-8 :

    {"version": 1, "magic": "ue_py", "source": "<uuid client>",
     "type": "<type>", "dest": "<uuid noeud>", "data": {...}}

Séquence complète :

  1. Le client rejoint le groupe multicast UDP 239.0.0.1:6766 et diffuse
     un `ping`.
  2. Chaque éditeur Unreal ayant l'exécution distante ACTIVÉE répond par un
     `pong` contenant son node_id et le nom de son projet.
  3. Le client ouvre un socket TCP en ÉCOUTE (c'est bien le client qui
     écoute, pas l'éditeur), puis envoie `open_connection` en UDP avec
     l'IP/port de ce socket.
  4. L'éditeur se connecte en retour sur ce socket TCP.
  5. Le client envoie `command` sur le canal TCP, l'éditeur répond
     `command_result` avec {success, result, output[]}.
  6. `close_connection` en UDP pour terminer proprement.

LIMITE DE VALIDATION — à lire avant de faire confiance à ce fichier
--------------------------------------------------------------------
Cette implémentation suit le protocole documenté, mais elle n'a PAS pu être
testée contre un éditeur Unreal réel dans l'environnement où elle a été
écrite (aucun Unreal disponible). Tout ce qui est vérifié ici par des tests
automatisés, c'est l'encodage/décodage des messages et la logique de repli.
Le comportement réseau réel reste à confirmer sur ta machine — et c'est
précisément pour ça que le repli copier-coller doit rester disponible.
"""

import json
import os
import socket
import struct
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple

PROTOCOL_VERSION = 1
PROTOCOL_MAGIC = "ue_py"

TYPE_PING = "ping"
TYPE_PONG = "pong"
TYPE_OPEN_CONNECTION = "open_connection"
TYPE_CLOSE_CONNECTION = "close_connection"
TYPE_COMMAND = "command"
TYPE_COMMAND_RESULT = "command_result"

MODE_EXEC_FILE = "ExecuteFile"
MODE_EXEC_STATEMENT = "ExecuteStatement"
MODE_EVAL_STATEMENT = "EvaluateStatement"

DEFAULT_MULTICAST_GROUP = ("239.0.0.1", 6766)
DEFAULT_MULTICAST_BIND = "0.0.0.0"
DEFAULT_MULTICAST_TTL = 0  # 0 = même machine uniquement (défaut d'Unreal)
DEFAULT_COMMAND_ENDPOINT = ("127.0.0.1", 6776)


def list_local_ipv4() -> List[str]:
    """Toutes les adresses IPv4 locales, l'adresse « par défaut » en tête.

    Sur Windows, une machine de dev a très souvent plusieurs interfaces
    (Wi-Fi, Ethernet, VPN, Hyper-V, WSL, VirtualBox…). Joindre un groupe
    multicast sur 0.0.0.0 laisse l'OS choisir UNE interface, et il choisit
    régulièrement une interface virtuelle sur laquelle Unreal n'émet pas :
    la découverte échoue alors alors que tout est correctement configuré.
    D'où l'énumération explicite, pour émettre et écouter sur toutes.
    """
    addresses: List[str] = []

    # Interface qui porterait le trafic sortant par défaut. Aucun paquet
    # n'est réellement envoyé par un connect() UDP — c'est juste une
    # résolution de route.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            addresses.append(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr not in addresses:
                addresses.append(addr)
    except (socket.gaierror, OSError):
        pass

    if "127.0.0.1" not in addresses:
        addresses.append("127.0.0.1")
    return addresses


def read_multicast_settings_from_ini(default_engine_ini: str) -> Dict[str, Any]:
    """Lit les réglages multicast réellement configurés dans le projet.

    Ces valeurs sont surchargeables par projet
    (RemoteExecutionMulticastGroupEndpoint, ...MulticastBindAddress,
    ...MulticastTtl). Utiliser les défauts en dur alors que le projet en
    déclare d'autres produirait exactement le symptôme observé : aucun
    nœud découvert malgré une configuration valide.
    """
    settings: Dict[str, Any] = {}
    if not default_engine_ini or not os.path.isfile(default_engine_ini):
        return settings
    try:
        with open(default_engine_ini, "r", encoding="utf-8-sig", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return settings

    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped == "[/Script/PythonScriptPlugin.PythonScriptPluginSettings]"
            continue
        if not in_section or "=" not in stripped or stripped.startswith(";"):
            continue
        key, _, raw = stripped.partition("=")
        key, raw = key.strip(), raw.strip().strip('"')
        if key == "RemoteExecutionMulticastGroupEndpoint" and ":" in raw:
            host, _, port = raw.rpartition(":")
            try:
                settings["multicast_group"] = (host, int(port))
            except ValueError:
                pass
        elif key == "RemoteExecutionMulticastBindAddress":
            settings["multicast_bind"] = raw
        elif key == "RemoteExecutionMulticastTtl":
            try:
                settings["multicast_ttl"] = int(raw)
            except ValueError:
                pass
    return settings


class RemoteExecutionError(RuntimeError):
    """Échec de transport (découverte, connexion, timeout) — distinct d'une
    commande qui s'exécute mais renvoie success=False, qui est un échec
    applicatif rendu dans CommandResult."""


@dataclass
class RemoteNode:
    """Un éditeur Unreal découvert sur le réseau local."""
    node_id: str
    project_name: str = ""
    project_root: str = ""
    engine_version: str = ""
    address: str = ""
    last_seen: float = 0.0

    def label(self) -> str:
        bits = [self.project_name or "(projet inconnu)"]
        if self.engine_version:
            bits.append(f"UE {self.engine_version}")
        if self.address:
            bits.append(self.address)
        return " — ".join(bits)


@dataclass
class CommandResult:
    """Résultat d'une commande exécutée dans l'éditeur.

    `success` est celui rendu par Unreal : False signifie que la commande a
    bien été reçue et exécutée, mais a levé une exception côté Unreal. Ce
    n'est PAS la même chose qu'un échec de transport, qui lève
    RemoteExecutionError.
    """
    success: bool
    result: str = ""
    output: List[Dict[str, str]] = field(default_factory=list)

    def output_text(self) -> str:
        lines = []
        for entry in self.output:
            text = str(entry.get("output", "")).rstrip("\n")
            if text:
                lines.append(text)
        return "\n".join(lines)


def _encode_message(source_id: str, msg_type: str,
                    dest_id: Optional[str] = None,
                    data: Optional[Dict[str, Any]] = None) -> bytes:
    payload: Dict[str, Any] = {
        "version": PROTOCOL_VERSION,
        "magic": PROTOCOL_MAGIC,
        "source": source_id,
        "type": msg_type,
    }
    if dest_id:
        payload["dest"] = dest_id
    if data is not None:
        payload["data"] = data
    return json.dumps(payload).encode("utf-8")


def _decode_message(raw: bytes) -> Optional[Dict[str, Any]]:
    """Renvoie None plutôt que de lever pour tout ce qui n'est pas un
    message valide de CE protocole — le groupe multicast peut très bien
    transporter autre chose, ce n'est pas une erreur."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("magic") != PROTOCOL_MAGIC:
        return None
    if payload.get("version") != PROTOCOL_VERSION:
        return None
    if not payload.get("type"):
        return None
    return payload


class RemoteExecutionClient:
    """Client d'exécution distante. À utiliser comme context manager :

        with RemoteExecutionClient() as client:
            nodes = client.discover_nodes(timeout=2.0)
            client.open_command_channel(nodes[0])
            res = client.run_statement("import unreal; print(unreal.__file__)")
    """

    def __init__(self,
                 multicast_group: Tuple[str, int] = DEFAULT_MULTICAST_GROUP,
                 multicast_bind: str = DEFAULT_MULTICAST_BIND,
                 multicast_ttl: int = DEFAULT_MULTICAST_TTL,
                 command_endpoint: Tuple[str, int] = DEFAULT_COMMAND_ENDPOINT,
                 interfaces: Optional[List[str]] = None):
        self.node_id = str(uuid.uuid4())
        self.multicast_group = multicast_group
        self.multicast_bind = multicast_bind
        self.multicast_ttl = multicast_ttl
        self.command_endpoint = command_endpoint
        # Interfaces sur lesquelles rejoindre le groupe et émettre. Par
        # défaut : toutes celles de la machine (voir list_local_ipv4).
        self.interfaces: List[str] = interfaces if interfaces is not None else list_local_ipv4()

        # Renseignés par start(), lus par le diagnostic réseau.
        self.joined_interfaces: List[str] = []
        self.interface_errors: List[str] = []

        self._broadcast_socket: Optional[socket.socket] = None
        self._listen_socket: Optional[socket.socket] = None
        self._command_socket: Optional[socket.socket] = None
        self._connected_node: Optional[RemoteNode] = None

    @classmethod
    def from_project(cls, default_engine_ini: str = "", **kwargs) -> "RemoteExecutionClient":
        """Construit un client en respectant les réglages multicast que le
        projet déclare, au lieu des seuls défauts en dur."""
        settings = read_multicast_settings_from_ini(default_engine_ini)
        settings.update(kwargs)
        return cls(**settings)

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    def __enter__(self) -> "RemoteExecutionClient":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def start(self) -> None:
        """Ouvre le socket multicast et rejoint le groupe sur TOUTES les
        interfaces locales.

        Rejoindre uniquement sur 0.0.0.0 laisse l'OS choisir une interface.
        Sur une machine Windows avec plusieurs cartes (VPN, Hyper-V, WSL,
        VirtualBox), il choisit souvent une interface virtuelle sur laquelle
        Unreal n'émet pas — la découverte renvoie alors zéro nœud alors que
        l'éditeur est bien lancé et correctement configuré. C'est la cause
        la plus fréquente d'un « aucun éditeur joignable » trompeur.
        """
        if self._broadcast_socket is not None:
            return
        self.joined_interfaces = []
        self.interface_errors = []
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # SO_REUSEPORT n'existe pas sous Windows : absence normale.
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.bind((self.multicast_bind, self.multicast_group[1]))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self.multicast_ttl)

            group_bin = socket.inet_aton(self.multicast_group[0])

            # Adhésion sur chaque interface. Une interface qui refuse n'est
            # pas une erreur fatale (adaptateur virtuel down, pas de support
            # multicast) — on note et on continue, l'important est qu'au
            # moins une adhésion réussisse.
            for addr in self.interfaces:
                try:
                    sock.setsockopt(
                        socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                        group_bin + socket.inet_aton(addr),
                    )
                    self.joined_interfaces.append(addr)
                except OSError as exc:
                    self.interface_errors.append(f"{addr}: {exc}")

            if not self.joined_interfaces:
                # Repli : adhésion générique via INADDR_ANY.
                try:
                    sock.setsockopt(
                        socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                        group_bin + socket.inet_aton("0.0.0.0"),
                    )
                    self.joined_interfaces.append("0.0.0.0")
                except OSError as exc:
                    self.interface_errors.append(f"0.0.0.0: {exc}")

            sock.settimeout(0.2)
            self._broadcast_socket = sock
        except OSError as exc:
            raise RemoteExecutionError(
                f"Impossible d'ouvrir le canal multicast {self.multicast_group[0]}:"
                f"{self.multicast_group[1]} — {exc}. "
                "Causes fréquentes : pare-feu Windows bloquant Python, "
                "ou un autre outil déjà à l'écoute sur ce port."
            ) from exc

    def stop(self) -> None:
        self.close_command_channel()
        if self._broadcast_socket is not None:
            try:
                self._broadcast_socket.close()
            except OSError:
                pass
            self._broadcast_socket = None

    # ------------------------------------------------------------------
    # Découverte
    # ------------------------------------------------------------------

    def discover_nodes(self, timeout: float = 2.0) -> List[RemoteNode]:
        """Diffuse un ping et collecte les pong pendant `timeout` secondes.

        Une liste vide ne veut pas dire « Unreal n'est pas lancé » — ça peut
        aussi être l'exécution distante désactivée dans les Project Settings,
        ou le pare-feu. C'est UnrealAuthorizationManager qui sait distinguer
        ces cas ; ici on se contente de rapporter ce qu'on a vu.
        """
        if self._broadcast_socket is None:
            self.start()
        assert self._broadcast_socket is not None

        ping = _encode_message(self.node_id, TYPE_PING)
        sent_from: List[str] = []
        send_errors: List[str] = []

        # Émission sur CHAQUE interface : un ping envoyé seulement sur
        # l'interface par défaut n'atteint pas un éditeur qui écoute sur une
        # autre (cas courant dès qu'un VPN ou Hyper-V est présent).
        for addr in (self.joined_interfaces or [self.multicast_bind]):
            try:
                if addr != "0.0.0.0":
                    self._broadcast_socket.setsockopt(
                        socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(addr),
                    )
                self._broadcast_socket.sendto(ping, self.multicast_group)
                sent_from.append(addr)
            except OSError as exc:
                send_errors.append(f"{addr}: {exc}")

        self.last_ping_interfaces = sent_from
        self.last_ping_errors = send_errors

        if not sent_from:
            raise RemoteExecutionError(
                "Échec d'envoi du ping multicast sur toutes les interfaces : "
                + " ; ".join(send_errors)
            )

        found: Dict[str, RemoteNode] = {}
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw, addr = self._broadcast_socket.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break

            message = _decode_message(raw)
            if message is None or message.get("type") != TYPE_PONG:
                continue
            source = message.get("source")
            if not source or source == self.node_id:
                continue

            data = message.get("data") or {}
            found[source] = RemoteNode(
                node_id=source,
                project_name=str(data.get("project_name", "")),
                project_root=str(data.get("project_root", "")),
                engine_version=str(data.get("engine_version", "")),
                address=addr[0] if addr else "",
                last_seen=time.time(),
            )

        return list(found.values())

    # ------------------------------------------------------------------
    # Canal de commande
    # ------------------------------------------------------------------

    def open_command_channel(self, node: RemoteNode, timeout: float = 5.0) -> None:
        """Ouvre le canal TCP vers `node`.

        Point contre-intuitif du protocole : c'est le CLIENT qui écoute et
        l'éditeur qui se connecte en retour. On met donc le socket d'écoute
        en place AVANT d'envoyer open_connection, sinon l'éditeur tente de
        se connecter dans le vide.
        """
        if self._broadcast_socket is None:
            self.start()
        self.close_command_channel()

        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Écoute sur 0.0.0.0, pas sur command_endpoint[0] : si l'éditeur
            # a été découvert via une autre interface que la loopback, une
            # écoute limitée à 127.0.0.1 refuserait sa connexion retour et
            # l'on obtiendrait un timeout incompréhensible (« découvert mais
            # ne se connecte pas »). On écoute partout, et on ANNONCE
            # l'adresse par laquelle ce nœud précis peut nous joindre.
            listener.bind(("0.0.0.0", self.command_endpoint[1]))
            listener.listen(1)
            listener.settimeout(timeout)
            self._listen_socket = listener
        except OSError as exc:
            raise RemoteExecutionError(
                f"Impossible d'écouter sur le port {self.command_endpoint[1]} "
                f"pour le canal de commande — {exc}"
            ) from exc

        # Adresse annoncée à l'éditeur : celle de l'interface par laquelle il
        # nous a répondu. Annoncer 127.0.0.1 à un éditeur joint via une autre
        # interface le ferait se connecter à SA propre loopback.
        advertised_ip = self.command_endpoint[0]
        if node.address and node.address not in ("127.0.0.1", "0.0.0.0"):
            local_match = self._local_address_towards(node.address)
            if local_match:
                advertised_ip = local_match

        assert self._broadcast_socket is not None
        open_msg = _encode_message(
            self.node_id, TYPE_OPEN_CONNECTION, dest_id=node.node_id,
            data={"command_ip": advertised_ip,
                  "command_port": self.command_endpoint[1]},
        )
        try:
            self._broadcast_socket.sendto(open_msg, self.multicast_group)
        except OSError as exc:
            self.close_command_channel()
            raise RemoteExecutionError(f"Échec d'envoi de open_connection : {exc}") from exc

        try:
            conn, _ = listener.accept()
        except (socket.timeout, OSError) as exc:
            self.close_command_channel()
            raise RemoteExecutionError(
                "L'éditeur Unreal a bien été découvert mais ne s'est pas connecté "
                f"en retour dans le délai imparti ({timeout}s). "
                "Vérifiez que le pare-feu autorise la connexion entrante sur "
                f"{self.command_endpoint[0]}:{self.command_endpoint[1]}."
            ) from exc

        conn.settimeout(timeout)
        self._command_socket = conn
        self._connected_node = node

    def _local_address_towards(self, remote_ip: str) -> str:
        """Adresse locale que l'OS utiliserait pour joindre `remote_ip`."""
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.connect((remote_ip, 9))
                return probe.getsockname()[0]
            finally:
                probe.close()
        except OSError:
            return ""

    def close_command_channel(self) -> None:
        if self._command_socket is not None:
            if self._connected_node is not None and self._broadcast_socket is not None:
                try:
                    self._broadcast_socket.sendto(
                        _encode_message(self.node_id, TYPE_CLOSE_CONNECTION,
                                        dest_id=self._connected_node.node_id),
                        self.multicast_group,
                    )
                except OSError:
                    pass
            try:
                self._command_socket.close()
            except OSError:
                pass
            self._command_socket = None
        self._connected_node = None

        if self._listen_socket is not None:
            try:
                self._listen_socket.close()
            except OSError:
                pass
            self._listen_socket = None

    # ------------------------------------------------------------------
    # Exécution
    # ------------------------------------------------------------------

    def run_command(self, command: str,
                    exec_mode: str = MODE_EXEC_STATEMENT,
                    unattended: bool = True,
                    timeout: float = 3600.0) -> CommandResult:
        """Envoie une commande et attend son résultat.

        `timeout` est volontairement très large par défaut : le scan du
        manifeste sur une grosse map prend plusieurs minutes, et couper au
        bout de 30s produirait un faux échec alors que l'éditeur travaille
        encore.
        """
        if self._command_socket is None:
            raise RemoteExecutionError(
                "Aucun canal de commande ouvert — appelez open_command_channel() d'abord."
            )

        payload = _encode_message(
            self.node_id, TYPE_COMMAND, dest_id=self._connected_node.node_id if self._connected_node else None,
            data={"command": command, "unattended": unattended, "exec_mode": exec_mode},
        )

        try:
            self._command_socket.sendall(payload)
        except OSError as exc:
            raise RemoteExecutionError(f"Échec d'envoi de la commande : {exc}") from exc

        message = self._receive_message(TYPE_COMMAND_RESULT, timeout=timeout)
        data = message.get("data") or {}
        return CommandResult(
            success=bool(data.get("success", False)),
            result=str(data.get("result", "")),
            output=list(data.get("output", []) or []),
        )

    def _receive_message(self, expected_type: str, timeout: float) -> Dict[str, Any]:
        """Accumule jusqu'à obtenir un JSON complet.

        Le protocole n'a pas de préfixe de longueur ni de délimiteur : un
        message peut arriver en plusieurs segments TCP, et un `recv` unique
        renverrait un JSON tronqué. On accumule donc et on retente le parse
        à chaque segment.
        """
        assert self._command_socket is not None
        self._command_socket.settimeout(timeout)

        buffer = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = self._command_socket.recv(65536)
            except socket.timeout:
                continue
            except OSError as exc:
                raise RemoteExecutionError(f"Canal de commande interrompu : {exc}") from exc

            if not chunk:
                raise RemoteExecutionError(
                    "L'éditeur Unreal a fermé le canal de commande avant de répondre. "
                    "Si l'éditeur s'est fermé ou a planté pendant l'étape, le rapport "
                    "de cette étape n'a pas été écrit."
                )

            buffer += chunk
            message = _decode_message(buffer)
            if message is None:
                continue  # JSON encore incomplet, on continue d'accumuler
            if message.get("type") != expected_type:
                buffer = b""
                continue
            return message

        raise RemoteExecutionError(
            f"Aucune réponse de type '{expected_type}' reçue au bout de {timeout}s."
        )


def probe_editor(timeout: float = 2.0, default_engine_ini: str = "") -> List[RemoteNode]:
    """Découverte ponctuelle, sans maintenir de connexion — utilisée par le
    module d'autorisations pour répondre à « un éditeur est-il joignable ? »."""
    try:
        with RemoteExecutionClient.from_project(default_engine_ini) as client:
            return client.discover_nodes(timeout=timeout)
    except RemoteExecutionError:
        return []


def diagnose_network(timeout: float = 3.0, default_engine_ini: str = "") -> List[str]:
    """Rapport détaillé, étape par étape, de ce qui se passe réellement.

    Existe parce qu'un « aucun éditeur joignable » a au moins cinq causes
    distinctes (éditeur fermé, exécution distante non activée, éditeur non
    redémarré depuis l'activation, pare-feu, mauvaise interface réseau) et
    que les distinguer par tâtonnement coûte cher. Chaque ligne dit ce qui a
    été tenté et ce qui a été observé — pas une conclusion devinée.
    """
    lines: List[str] = []

    settings = read_multicast_settings_from_ini(default_engine_ini)
    if settings:
        lines.append(f"Réglages multicast lus dans le projet : {settings}")
    else:
        lines.append(
            "Aucun réglage multicast personnalisé dans le projet — "
            f"utilisation des défauts {DEFAULT_MULTICAST_GROUP[0]}:{DEFAULT_MULTICAST_GROUP[1]}."
        )

    interfaces = list_local_ipv4()
    lines.append(f"Interfaces IPv4 locales détectées ({len(interfaces)}) : {', '.join(interfaces)}")
    if len(interfaces) > 2:
        lines.append(
            "  Plusieurs interfaces présentes (VPN / Hyper-V / WSL / VirtualBox ?). "
            "Le ping est envoyé sur chacune."
        )

    try:
        client = RemoteExecutionClient.from_project(default_engine_ini)
    except Exception as exc:
        lines.append(f"✕ Création du client impossible : {exc}")
        return lines

    try:
        client.start()
    except RemoteExecutionError as exc:
        lines.append(f"✕ Ouverture du socket multicast refusée : {exc}")
        lines.append(
            "  → Typiquement le pare-feu Windows. Autorisez python.exe / "
            "pythonw.exe sur les réseaux privés."
        )
        return lines

    lines.append(f"✓ Socket multicast ouvert sur le port {client.multicast_group[1]}.")
    if client.joined_interfaces:
        lines.append(f"✓ Groupe rejoint sur : {', '.join(client.joined_interfaces)}")
    if client.interface_errors:
        lines.append(f"  Interfaces refusées (normal pour un adaptateur inactif) : "
                     f"{'; '.join(client.interface_errors)}")

    try:
        nodes = client.discover_nodes(timeout=timeout)
    except RemoteExecutionError as exc:
        lines.append(f"✕ Envoi du ping en échec : {exc}")
        client.stop()
        return lines

    sent = getattr(client, "last_ping_interfaces", [])
    if sent:
        lines.append(f"✓ Ping émis depuis : {', '.join(sent)}")
    for err in getattr(client, "last_ping_errors", []):
        lines.append(f"  Émission refusée sur {err}")

    if nodes:
        lines.append(f"✓ {len(nodes)} éditeur(s) ont répondu :")
        for node in nodes:
            lines.append(f"    • {node.label()}")
    else:
        lines.append(f"✕ Aucune réponse après {timeout}s d'écoute.")
        lines.append("  Causes possibles, dans l'ordre de fréquence :")
        lines.append("   1. L'éditeur n'a pas été REDÉMARRÉ depuis l'activation de "
                     "l'exécution distante (le réglage n'est lu qu'au démarrage).")
        lines.append("   2. « Enable Remote Execution » n'est pas coché dans "
                     "Project Settings > Plugins > Python.")
        lines.append("   3. Le pare-feu Windows bloque python.exe en entrée ou en sortie.")
        lines.append("   4. L'éditeur est lancé mais la map n'est pas encore chargée.")

    client.stop()
    return lines
