"""Command-line interface. Every current command is local and read-only."""

from __future__ import annotations

import argparse
import sys

from .boot import BootPreflightError, preflight as boot_preflight
from .build import BuildError, build_all, boot_flow, load_config
from pathlib import Path
from .device import DeviceProbeError, probe
from .devicetree import DeviceTreeError, diff as diff_devicetree, inspect as inspect_devicetree
from .complzss import ComplzssError, decompress as decompress_complzss
from .firmware import FirmwareVerificationError, REQUIREMENTS, verify
from .host import DFU_PRODUCT, PONGO_PRODUCT, readiness as host_readiness
from .img4 import Im4pError, inspect as inspect_im4p, unpack as unpack_im4p
from .macho import MachOError, inspect as inspect_macho
from .manifest import ManifestError, component_delta, extract_component, find_identity, inventory
from .prelink import (
    PrelinkError,
    delta as driver_delta,
    extract_driver,
    inspect as inspect_drivers,
    inspect_record,
)
from .pongo import PongoError, boot_raw as pongo_boot_raw, load_xnu as pongo_load_xnu
from .session import PongoSessionError, start_palera1n_pongo, start_pongo


def _print_inventory(path: str) -> int:
    report = inventory(path)
    print(f"IPSW: {report.path}")
    print(f"Version: {report.product_version} ({report.product_build_version})")
    for identity in report.identities:
        print(
            f"- {identity.board_config}: {identity.product_type} "
            f"{identity.build_train} {identity.build_number} "
            f"[{identity.restore_behavior or 'n/a'}] "
            f"({len(identity.components)} artefacts"
            f"{f', {len(identity.unmapped_components)} entrées sans chemin' if identity.unmapped_components else ''})"
        )
    return 0


def _compare(
    target_path: str,
    donor_path: str,
    target_board: str,
    donor_board: str,
    restore_behavior: str,
) -> int:
    target_report = inventory(target_path)
    donor_report = inventory(donor_path)
    target = find_identity(target_report, target_board, restore_behavior)
    donor = find_identity(donor_report, donor_board, restore_behavior)
    only_target, only_donor = component_delta(target, donor)
    common = len(set(target.components) & set(donor.components))

    print(f"Cible : {target.board_config} dans {target_report.path}")
    print(f"Donneur : {donor.board_config} dans {donor_report.path}")
    print(f"Composants communs : {common}")
    print("Uniquement cible : " + (", ".join(only_target) or "aucun"))
    print("Uniquement donneur : " + (", ".join(only_donor) or "aucun"))
    return 0


def _probe() -> int:
    facts = probe()
    print(f"Produit : {facts.product_type}")
    print(f"Modèle matériel : {facts.hardware_model}")
    print(f"iOS : {facts.product_version} ({facts.build_version})")
    return 0


def _verify(ipsw: str, role: str) -> int:
    result = verify(ipsw, role)
    print(f"Entrée validée : {result.requirement.role}")
    print(f"Version : {result.report.product_version} ({result.report.product_build_version})")
    print(f"SHA-1 : {result.actual_sha1}")
    print("Cartes utiles : " + ", ".join(result.matching_boards))
    return 0


def _extract(ipsw: str, board: str, component: str, output: str, restore_behavior: str) -> int:
    identity = find_identity(inventory(ipsw), board, restore_behavior)
    path = extract_component(ipsw, identity, component, output)
    print(f"Artefact extrait : {path}")
    return 0


def _inspect_im4p(path: str) -> int:
    info = inspect_im4p(path)
    print(f"Type : {info.image_type}")
    print(f"Description : {info.description}")
    print(f"Charge utile : offset {info.payload_offset}, {info.payload_length} octets")
    print(f"Compression : {info.compression}")
    return 0


def _unpack_im4p(path: str, output: str) -> int:
    info = unpack_im4p(path, output)
    print(f"Charge utile extraite ({info.compression}) : {output}")
    return 0


def _decompress_complzss(path: str, output: str) -> int:
    info = decompress_complzss(path, output)
    print(
        f"KernelCache décompressé : {output} "
        f"({info.uncompressed_size} octets, complzss v{info.version})"
    )
    return 0


def _inspect_macho(path: str) -> int:
    info = inspect_macho(path)
    header = info.header
    print(f"CPU : 0x{header.cputype:x}/0x{header.cpusubtype:x}; type {header.filetype}")
    print(f"Commandes : {header.ncmds}; drapeaux : 0x{header.flags:x}")
    for segment in info.segments:
        print(
            f"{segment.name}: vm=0x{segment.vmaddr:x}+0x{segment.vmsize:x} "
            f"fichier=0x{segment.fileoff:x}+0x{segment.filesize:x} sections={segment.nsects}"
        )
    for entry in info.filesets:
        print(f"fileset {entry.identifier}: vm=0x{entry.vmaddr:x} fichier=0x{entry.fileoff:x}")
    return 0


def _inspect_devicetree(path: str) -> int:
    info = inspect_devicetree(path)
    print(f"Racine : {info.root.name}")
    print(f"Nœuds : {info.node_count}; propriétés : {info.property_count}; taille : {info.consumed_size} octets")
    return 0


def _diff_devicetree(target_path: str, donor_path: str) -> int:
    result = diff_devicetree(inspect_devicetree(target_path), inspect_devicetree(donor_path))
    print(f"Nœuds uniquement cible : {len(result.only_target_nodes)}")
    for path in result.only_target_nodes:
        print(f"- cible {path}")
    print(f"Nœuds uniquement donneur : {len(result.only_donor_nodes)}")
    for path in result.only_donor_nodes:
        print(f"- donneur {path}")
    print(f"Nœuds communs modifiés : {len(result.changed_nodes)}")
    print(f"Propriétés différentes : {result.changed_property_count}")
    return 0


def _inspect_drivers(path: str) -> int:
    info = inspect_drivers(path)
    print(f"Pilotes préliés uniques : {len(info.bundle_identifiers)}")
    for identifier in info.bundle_identifiers:
        print(f"- {identifier}")
    return 0


def _inspect_driver(path: str, bundle_identifier: str) -> int:
    info = inspect_record(path, bundle_identifier)
    print(f"Pilote : {bundle_identifier}")
    if info.executable_size is None:
        print("Aucun exécutable déclaré")
    else:
        print(f"Adresse préliée : 0x{info.executable_load_address:x}")
        print(f"Taille exécutable : 0x{info.executable_size:x}")
    print("Dépendances : " + ", ".join(info.dependencies))
    print("Personnalités IOKit : " + ", ".join(info.personalities))
    return 0


def _extract_driver(path: str, bundle_identifier: str, output: str) -> int:
    result = extract_driver(path, bundle_identifier, output)
    print(f"Pilote extrait : {result}")
    return 0


def _diff_drivers(target_path: str, donor_path: str) -> int:
    target = inspect_drivers(target_path)
    donor = inspect_drivers(donor_path)
    only_target, only_donor = driver_delta(target, donor)
    print(f"Pilotes uniquement cible : {len(only_target)}")
    for identifier in only_target:
        print(f"- cible {identifier}")
    print(f"Pilotes uniquement donneur : {len(only_donor)}")
    for identifier in only_donor:
        print(f"- donneur {identifier}")
    return 0


def _boot_preflight(
    target_tree_im4p: str,
    target_tree_raw: str,
    donor_kernel_im4p: str,
    donor_kernel_raw: str,
    policy: str,
) -> int:
    result = boot_preflight(target_tree_im4p, target_tree_raw, donor_kernel_im4p, donor_kernel_raw, policy)
    print(f"DeviceTree cible : {result.target_board} / {result.target_model}")
    print(f"SHA-256 DeviceTree : {result.target_tree_sha256}")
    print(f"SHA-256 KernelCache donneur : {result.donor_kernel_sha256}")
    print(f"SHA-256 Mach-O donneur : {result.donor_kernel_raw_sha256}")
    if result.missing_tools:
        print("Outils hôte absents : " + ", ".join(result.missing_tools))
        print("Prévalidation réussie, mais aucune commande USB ne peut être exécutée.")
    else:
        print("Prévalidation réussie ; les outils hôte sont disponibles.")
    return 0


def _host_readiness() -> int:
    result = host_readiness()
    print("Outils présents : " + (", ".join(result.tools_present) or "aucun"))
    print("Outils absents : " + (", ".join(result.tools_missing) or "aucun"))
    if not result.apple_devices:
        print("Aucun appareil Apple USB détecté.")
    for device in result.apple_devices:
        if device.product == DFU_PRODUCT:
            mode = "DFU"
        elif device.product == PONGO_PRODUCT:
            mode = "chargeur RAM/Pongo"
        else:
            mode = "mode Apple inconnu"
        print(f"Apple USB : {device.path.name} {device.vendor}:{device.product} ({mode})")
    return 0


def _pongo_boot_raw(image: str, timeout: float) -> int:
    size = pongo_boot_raw(image, timeout)
    print(f"Image brute envoyée à Pongo : {image} ({size} octets)")
    print("Commande bootr envoyée ; le démarrage du noyau reste à confirmer sur l’appareil.")
    return 0


def _pongo_load_xnu(image: str, timeout: float) -> int:
    size = pongo_load_xnu(image, timeout)
    print(f"KernelCache XNU transmis : {image} ({size} octets)")
    print("loadx et bootux envoyés ; le SE quitte Pongo vers le noyau XNU.")
    return 0


def _start_pongo(image: str, log: str, loader: str) -> int:
    if loader == "palera1n":
        print("Démarrage Pongo seul via palera1n ; aucun mode jailbreak n'est sélectionné.")
        print(f"Journal : {log}")
        return start_palera1n_pongo(image, log)
    print("Démarrage de la session DFU/Pongo ; suivre les instructions checkra1n.")
    print(f"Journal : {log}")
    return start_pongo(image, log)


def _build(config_path: str, im4m: str | None, boot: bool) -> int:
    """Build all boot images and optionally boot."""
    config = load_config(config_path)
    im4m_path = Path(im4m) if im4m else None

    if im4m_path and not im4m_path.exists():
        print(f"IM4M not found: {im4m_path}")
        return 1

    try:
        artifacts = build_all(config, im4m_path)
        if boot:
            return boot_flow(artifacts, config)
        return 0
    except BuildError as e:
        print(f"Build error: {e}")
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}")
        return 1


def _extract_im4m(nor_dump: str, output: str) -> int:
    """Extract IM4M (APTicket) from NOR dump."""
    from .img4_tool import get_img4_tool
    img4 = get_img4_tool()
    img4.extract_im4m(Path(nor_dump), Path(output))
    print(f"IM4M extracted to: {output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ios16se",
        description="Analyse des IPSW et préparation contrôlée d'une session N69AP.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory_parser = subparsers.add_parser("inventory", help="affiche les identités d'un IPSW")
    inventory_parser.add_argument("ipsw")
    subparsers.add_parser("probe", help="lit les propriétés d'un appareil démarré, sans le modifier")
    verify_parser = subparsers.add_parser("verify", help="valide un IPSW téléchargé contre les entrées épinglées")
    verify_parser.add_argument("role", choices=sorted(REQUIREMENTS))
    verify_parser.add_argument("ipsw")
    extract_parser = subparsers.add_parser("extract", help="copie un seul composant déclaré par le manifest")
    extract_parser.add_argument("ipsw")
    extract_parser.add_argument("board")
    extract_parser.add_argument("component")
    extract_parser.add_argument("output")
    extract_parser.add_argument(
        "--restore-behavior",
        choices=("Erase", "Update"),
        default="Erase",
        help="identité IPSW à utiliser (défaut : Erase)",
    )
    inspect_parser = subparsers.add_parser("inspect-im4p", help="lit les métadonnées d'un IM4P local")
    inspect_parser.add_argument("im4p")
    unpack_parser = subparsers.add_parser("unpack-im4p", help="extrait la charge utile opaque d'un IM4P")
    unpack_parser.add_argument("im4p")
    unpack_parser.add_argument("output")
    decompress_parser = subparsers.add_parser("decompress-complzss", help="décompresse un KernelCache Apple legacy")
    decompress_parser.add_argument("input")
    decompress_parser.add_argument("output")
    macho_parser = subparsers.add_parser("inspect-macho", help="liste les segments et filesets d'un KernelCache brut")
    macho_parser.add_argument("macho")
    tree_parser = subparsers.add_parser("inspect-devicetree", help="lit un DeviceTree Apple décompressé")
    tree_parser.add_argument("tree")
    tree_diff_parser = subparsers.add_parser("diff-devicetree", help="compare deux DeviceTree Apple décompressés")
    tree_diff_parser.add_argument("target_tree")
    tree_diff_parser.add_argument("donor_tree")
    drivers_parser = subparsers.add_parser("inspect-drivers", help="liste les pilotes préliés d'un KernelCache brut")
    drivers_parser.add_argument("macho")
    driver_parser = subparsers.add_parser("inspect-driver", help="lit les dépendances et la plage d'un pilote prélié")
    driver_parser.add_argument("macho")
    driver_parser.add_argument("bundle_identifier")
    extract_driver_parser = subparsers.add_parser("extract-driver", help="extrait un Mach-O de pilote prélié")
    extract_driver_parser.add_argument("macho")
    extract_driver_parser.add_argument("bundle_identifier")
    extract_driver_parser.add_argument("output")
    drivers_diff_parser = subparsers.add_parser("diff-drivers", help="compare les pilotes préliés de deux KernelCaches")
    drivers_diff_parser.add_argument("target_macho")
    drivers_diff_parser.add_argument("donor_macho")
    boot_parser = subparsers.add_parser(
        "boot-preflight",
        help="valide les rôles N69AP/iPadOS 16 avant une future session DFU ; n'envoie rien par USB",
    )
    boot_parser.add_argument("target_tree_im4p")
    boot_parser.add_argument("target_tree_raw")
    boot_parser.add_argument("donor_kernel_im4p")
    boot_parser.add_argument("donor_kernel_raw")
    boot_parser.add_argument(
        "--policy",
        default="profiles/n69ap.toml",
        help="profil d'empreintes N69AP à imposer (défaut : profiles/n69ap.toml)",
    )
    subparsers.add_parser("host-readiness", help="lit les outils et modes USB Apple locaux ; n'envoie rien")
    raw_boot_parser = subparsers.add_parser(
        "pongo-boot-raw",
        help="envoie une image brute à Pongo puis demande bootr (expérimental, modifie l’état RAM)",
    )
    raw_boot_parser.add_argument("image", help="image brute de démarrage")
    raw_boot_parser.add_argument("--timeout", type=float, default=5.0, help="attente USB Pongo en secondes")
    xnu_boot_parser = subparsers.add_parser(
        "pongo-load-xnu",
        help="charge un KernelCache Mach-O dans Pongo puis exécute loadx/bootux (RAM expérimentale)",
    )
    xnu_boot_parser.add_argument("kernelcache", help="KernelCache Mach-O brut")
    xnu_boot_parser.add_argument("--timeout", type=float, default=5.0, help="attente USB Pongo en secondes")
    pongo_parser = subparsers.add_parser(
        "start-pongo",
        help="charge PongoOS en RAM via palera1n ou checkra1n et journalise la session DFU",
    )
    pongo_parser.add_argument("--image", default="tools/pongo/Pongo.bin", help="image PongoOS à charger")
    pongo_parser.add_argument("--log", required=True, help="fichier journal de session à créer ou compléter")
    pongo_parser.add_argument(
        "--loader",
        choices=("palera1n", "checkra1n"),
        default="palera1n",
        help="chargeur RAM (défaut : palera1n, mode Pongo seul)",
    )
    compare_parser = subparsers.add_parser("compare", help="compare une cible et un donneur")
    compare_parser.add_argument("target_ipsw")
    compare_parser.add_argument("donor_ipsw")
    compare_parser.add_argument("--target-board", default="n69ap")
    compare_parser.add_argument("--donor-board", required=True)
    compare_parser.add_argument(
        "--restore-behavior",
        choices=("Erase", "Update"),
        default="Erase",
        help="identités IPSW à comparer (défaut : Erase)",
    )

    # Build commands
    build_parser = subparsers.add_parser("build", help="construire les 5 images de boot tethered")
    build_parser.add_argument("config", help="fichier de configuration TOML (ex: profiles/n69ap_ios16.toml)")
    build_parser.add_argument("--im4m", help="chemin vers IM4M.bin (APTicket)")
    build_parser.add_argument("--boot", action="store_true", help="démarrer l'appareil après construction")

    extract_im4m_parser = subparsers.add_parser("extract-im4m", help="extraire l'IM4M (APTicket) depuis un dump NOR")
    extract_im4m_parser.add_argument("nor_dump", help="fichier dump NOR (4 MB depuis /dev/disk2)")
    extract_im4m_parser.add_argument("output", help="fichier IM4M.bin de sortie")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inventory":
            return _print_inventory(args.ipsw)
        if args.command == "probe":
            return _probe()
        if args.command == "verify":
            return _verify(args.ipsw, args.role)
        if args.command == "extract":
            return _extract(args.ipsw, args.board, args.component, args.output, args.restore_behavior)
        if args.command == "inspect-im4p":
            return _inspect_im4p(args.im4p)
        if args.command == "unpack-im4p":
            return _unpack_im4p(args.im4p, args.output)
        if args.command == "decompress-complzss":
            return _decompress_complzss(args.input, args.output)
        if args.command == "inspect-macho":
            return _inspect_macho(args.macho)
        if args.command == "inspect-devicetree":
            return _inspect_devicetree(args.tree)
        if args.command == "diff-devicetree":
            return _diff_devicetree(args.target_tree, args.donor_tree)
        if args.command == "inspect-drivers":
            return _inspect_drivers(args.macho)
        if args.command == "inspect-driver":
            return _inspect_driver(args.macho, args.bundle_identifier)
        if args.command == "extract-driver":
            return _extract_driver(args.macho, args.bundle_identifier, args.output)
        if args.command == "diff-drivers":
            return _diff_drivers(args.target_macho, args.donor_macho)
        if args.command == "boot-preflight":
            return _boot_preflight(
                args.target_tree_im4p,
                args.target_tree_raw,
                args.donor_kernel_im4p,
                args.donor_kernel_raw,
                args.policy,
            )
        if args.command == "host-readiness":
            return _host_readiness()
        if args.command == "pongo-boot-raw":
            return _pongo_boot_raw(args.image, args.timeout)
        if args.command == "pongo-load-xnu":
            return _pongo_load_xnu(args.kernelcache, args.timeout)
        if args.command == "start-pongo":
            return _start_pongo(args.image, args.log, args.loader)
        if args.command == "build":
            return _build(args.config, args.im4m, args.boot)
        if args.command == "extract-im4m":
            return _extract_im4m(args.nor_dump, args.output)
        return _compare(
            args.target_ipsw,
            args.donor_ipsw,
            args.target_board,
            args.donor_board,
            args.restore_behavior,
        )
    except (
        ManifestError,
        DeviceProbeError,
        DeviceTreeError,
        BootPreflightError,
        FirmwareVerificationError,
        Im4pError,
        ComplzssError,
        MachOError,
        PrelinkError,
        PongoSessionError,
        PongoError,
        BuildError,
        FileNotFoundError,
    ) as error:
        print(f"Erreur : {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
