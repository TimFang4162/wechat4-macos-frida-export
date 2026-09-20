#!/usr/bin/env python3
"""构建内嵌 Frida Gadget 的微信调试副本，不修改 /Applications。"""

import argparse
import hashlib
import json
import lzma
import os
import pathlib
import plistlib
import shutil
import subprocess
import sys
import tempfile

from inject_load_dylib import inject
from sip_config import (
    BASE, DEBUG_BUNDLE_ID, DEBUG_BUNDLE_NAME, DEFAULT_SOURCE,
    DEFAULT_TARGET, RUNTIME,
)

GADGET_VERSION = "17.18.0"
GADGET_SHA256 = "7b2f0b21f8de23c00531355703ca99a99a39b06ec534d1fabf27aca87a765c81"
BUILD_SCHEMA = 4
LOAD_PATH = "@executable_path/../Frameworks/FridaGadget.dylib"


def run(*args, capture=False):
    return subprocess.run(
        [str(arg) for arg in args], check=True,
        text=capture, capture_output=capture,
    )


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_identity(app):
    info_path = app / "Contents" / "Info.plist"
    executable = app / "Contents" / "MacOS" / "WeChat"
    if not info_path.is_file() or not executable.is_file():
        raise RuntimeError(f"微信包不完整: {app}")
    with open(info_path, "rb") as stream:
        info = plistlib.load(stream)
    if info.get("CFBundleIdentifier") != "com.tencent.xinWeChat":
        raise RuntimeError(f"Bundle ID 不匹配: {info.get('CFBundleIdentifier')}")
    return {
        "source": str(app.resolve()),
        "version": info.get("WeChatBundleVersion")
                   or info.get("CFBundleShortVersionString"),
        "build": info.get("CFBundleVersion"),
        "main_sha256": sha256(executable),
        "gadget_version": GADGET_VERSION,
        "build_schema": BUILD_SCHEMA,
        "debug_bundle_id": DEBUG_BUNDLE_ID,
    }


def download_gadget(version):
    cache = RUNTIME / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    dylib = cache / f"frida-gadget-{version}-macos-universal.dylib"
    if dylib.is_file():
        if sha256(dylib) != GADGET_SHA256:
            raise RuntimeError(f"缓存中的 Frida Gadget 摘要不匹配: {dylib}")
        return dylib
    archive = pathlib.Path(str(dylib) + ".xz")
    url = (
        f"https://github.com/frida/frida/releases/download/{version}/"
        f"frida-gadget-{version}-macos-universal.dylib.xz"
    )
    print(f"[*] 下载 Frida Gadget {version} ...")
    run("curl", "--fail", "--location", "--retry", "3",
        "--output", archive, url)
    with lzma.open(archive, "rb") as source, open(dylib, "wb") as target:
        shutil.copyfileobj(source, target)
    dylib.chmod(0o755)
    actual = sha256(dylib)
    if actual != GADGET_SHA256:
        dylib.unlink(missing_ok=True)
        raise RuntimeError(
            f"Frida Gadget SHA-256 不匹配: expected={GADGET_SHA256}, actual={actual}")
    return dylib


def code_candidates(app):
    suffixes = (".framework", ".dylib", ".bundle", ".xpc", ".appex", ".app")
    code = []
    for path in (app / "Contents").rglob("*"):
        if (path.is_file() and path.name.endswith(".dylib")) or (
                path.is_dir() and path.name.endswith(suffixes)):
            code.append(path)
    return code


def capture_entitlements(app):
    snapshots = {}
    for path in code_candidates(app) + [app]:
        result = subprocess.run(
            ["codesign", "-d", "--entitlements", "-", "--xml", str(path)],
            text=False, capture_output=True)
        if result.returncode != 0 or not result.stdout.startswith(b"<?xml"):
            continue
        try:
            entitlements = plistlib.loads(result.stdout)
        except plistlib.InvalidFileException:
            continue
        if not isinstance(entitlements, dict) or not entitlements:
            continue
        # ad-hoc 签名没有 Tencent Team ID，不能继续声明官方容器身份；否则
        # libsecinit 会在沙盒注册阶段阻塞/拒绝。调试副本以非沙盒进程运行，
        # 数据仍受当前用户的 POSIX/TCC 权限约束。
        for identity_key in (
            "com.apple.application-identifier",
            "com.apple.security.application-groups",
            "com.apple.security.app-sandbox",
            "keychain-access-groups",
        ):
            entitlements.pop(identity_key, None)
        # 原官方 entitlement 中带 Tencent Team ID，而 ad-hoc 子模块没有 Team ID。
        # 关闭 library validation 可避免 AMFI 因 Team ID 不同拒载；微信登录阶段
        # 还会使用非 MAP_JIT 的匿名 RX trampoline，需要额外允许该内存类型。
        entitlements["com.apple.security.cs.disable-library-validation"] = True
        entitlements["com.apple.security.cs.allow-unsigned-executable-memory"] = True
        snapshots[path.relative_to(app).as_posix()] = entitlements
    return snapshots


def codesign(path, entitlements=None, deep=False):
    args = ["codesign", "--force"]
    if deep:
        args.append("--deep")
    args += ["--preserve-metadata=identifier,flags,runtime",
             "--sign", "-", "--timestamp=none"]
    temporary = None
    try:
        if entitlements:
            fd, temporary = tempfile.mkstemp(prefix="wechat-entitlements-", suffix=".plist")
            with os.fdopen(fd, "wb") as stream:
                plistlib.dump(entitlements, stream)
            args += ["--force-library-entitlements", "--entitlements", temporary]
        args.append(str(path))
        result = subprocess.run(
            [str(item) for item in args], text=True, capture_output=True)
        if result.returncode != 0:
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
            raise subprocess.CalledProcessError(
                result.returncode, args, result.stdout, result.stderr)
    finally:
        if temporary:
            pathlib.Path(temporary).unlink(missing_ok=True)


def sign_bundle(app, gadget, snapshots):
    # 微信资源中可能包含 immutable/只读缓存；xattr 清理只影响 Gatekeeper
    # 元数据，不作为签名成败条件。最终以 codesign --verify 为准。
    for attribute in ("com.apple.quarantine", "com.apple.provenance"):
        subprocess.run(
            ["xattr", "-dr", attribute, str(app)],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    codesign(gadget)
    for path in sorted(code_candidates(app),
                       key=lambda item: len(item.parts), reverse=True):
        relative = path.relative_to(app).as_posix()
        codesign(path, snapshots.get(relative))
    codesign(app, snapshots.get("."))
    run("codesign", "--verify", "--deep", "--strict", app)


def is_current(target, identity):
    marker = RUNTIME / "wechat-sip-build.json"
    executable = target / "Contents" / "MacOS" / "WeChat"
    gadget = target / "Contents" / "Frameworks" / "FridaGadget.dylib"
    if not marker.is_file() or not executable.is_file() or not gadget.is_file():
        return False
    try:
        recorded = json.loads(marker.read_text())
        if recorded != identity:
            return False
        listing = run("otool", "-L", executable, capture=True).stdout
        run("codesign", "--verify", "--deep", "--strict", target)
        return LOAD_PATH in listing
    except (OSError, ValueError, subprocess.CalledProcessError):
        return False


def build(source, target, force=False):
    RUNTIME.mkdir(exist_ok=True)
    identity = source_identity(source)
    if not force and is_current(target, identity):
        print(f"[+] SIP 调试副本已是最新: {target}")
        return

    gadget_source = download_gadget(GADGET_VERSION)
    temp_root = pathlib.Path(tempfile.mkdtemp(prefix="wechat-sip-", dir=RUNTIME))
    temp_app = temp_root / target.name
    try:
        print(f"[*] 复制官方微信（只读源）: {source}")
        run("ditto", source, temp_app)
        snapshots = capture_entitlements(temp_app)
        info_path = temp_app / "Contents" / "Info.plist"
        with open(info_path, "rb") as stream:
            debug_info = plistlib.load(stream)
        debug_info["CFBundleIdentifier"] = DEBUG_BUNDLE_ID
        debug_info["CFBundleName"] = DEBUG_BUNDLE_NAME
        debug_info["CFBundleDisplayName"] = DEBUG_BUNDLE_NAME
        with open(info_path, "wb") as stream:
            plistlib.dump(debug_info, stream)
        executable = temp_app / "Contents" / "MacOS" / "WeChat"
        frameworks = temp_app / "Contents" / "Frameworks"
        resources = temp_app / "Contents" / "Resources"
        gadget = frameworks / "FridaGadget.dylib"
        config_resource = resources / "FridaGadget.config"
        config_link = frameworks / "FridaGadget.config"

        frameworks.mkdir(exist_ok=True)
        shutil.copy2(gadget_source, gadget)
        gadget.chmod(0o755)
        config_resource.write_text(json.dumps({
            "interaction": {
                "type": "listen",
                "address": "127.0.0.1",
                "port": 27042,
                "on_port_conflict": "fail",
                "on_load": "wait",
            }
        }, indent=2) + "\n")
        config_link.unlink(missing_ok=True)
        config_link.symlink_to("../Resources/FridaGadget.config")

        if inject(executable, LOAD_PATH):
            print(f"[+] 已向双架构主程序注入 {LOAD_PATH}")
        sign_bundle(temp_app, gadget, snapshots)

        if target.exists():
            shutil.rmtree(target)
        temp_app.rename(target)
        (RUNTIME / "wechat-sip-build.json").write_text(
            json.dumps(identity, ensure_ascii=False, indent=2) + "\n"
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
    print(f"[+] 构建完成: {target}")


def main():
    parser = argparse.ArgumentParser(
        description="构建 SIP 开启时可使用的 Frida Gadget 微信调试副本")
    parser.add_argument("--source", type=pathlib.Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=pathlib.Path, default=DEFAULT_TARGET)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("仅支持 macOS")
    try:
        build(args.source.resolve(), args.target.resolve(), args.force)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[!] 构建失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
