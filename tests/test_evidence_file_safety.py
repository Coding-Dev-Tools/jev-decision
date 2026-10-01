"""Opened-file validation withstands path replacement without provider calls."""
import hashlib
import os
import subprocess

import pytest

from jev_decision import evidence_file
from jev_decision.evidence import read_evidence_file
from jev_decision.runtime import RuntimeConfig, RuntimeConfigError


def _directory_link(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            pytest.skip("Directory links unavailable on this filesystem")
    # Junctions exercise Windows parent replacement without symlink privileges.
    result = subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/c", "mklink", "/J",
                             str(link), str(target)], capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    if result.returncode:
        pytest.skip("Windows directory junction creation unavailable")


def _no_reads(monkeypatch):
    calls = []
    original = os.read
    def read(descriptor, limit):
        calls.append(descriptor)
        return original(descriptor, limit)
    monkeypatch.setattr(evidence_file.os, "read", read)
    return calls


class NoProvider:
    def __init__(self):
        self.calls = []

    def evaluate(self, *_args, **_kwargs):
        self.calls.append(1)
        raise AssertionError("A rejected file must not reach scoring")


@pytest.mark.parametrize("name", [".npmrc", ".pypirc", ".netrc", "_netrc", ".git-credentials",
                                 "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "vault.dpapi",
                                 "vault.jks", ".kube/config", ".docker/config.json"])
def test_sensitive_file_names_never_read_or_reach_scoring(tmp_path, monkeypatch, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b"synthetic-private-canary\n"
    path.write_bytes(original)
    reads = _no_reads(monkeypatch)
    client = NoProvider()
    with pytest.raises(ValueError, match="credential_or_private_file_denied"):
        read_evidence_file(str(path), "inspect", [tmp_path], mode="shadow", client=client)
    assert reads == [] and client.calls == [] and path.read_bytes() == original


def test_platform_name_aliases_do_not_bypass_private_file_filters(tmp_path, monkeypatch):
    reads = _no_reads(monkeypatch)
    if os.name == "nt":
        for name in (".npmrc. ", ".NETRC ", "id_rsa.", ".kube./config", "build.log:hidden"):
            with pytest.raises(ValueError, match="credential_or_private_file_denied"):
                read_evidence_file(str(tmp_path / name), "inspect", [tmp_path])
        assert reads == []
    else:
        # Colons are ordinary filename characters on POSIX, not NTFS streams.
        path = tmp_path / "build:log"
        path.write_bytes(b"INFO ordinary evidence\n")
        result = read_evidence_file(str(path), "inspect", [tmp_path])
        assert result["output"] == "INFO ordinary evidence\n" and reads


def test_replaced_approved_root_never_grants_access_on_read_or_reload(tmp_path, monkeypatch):
    approved, outside = tmp_path / "approved", tmp_path / "outside"
    approved.mkdir()
    outside.mkdir()
    config = RuntimeConfig(home=RuntimeConfig.load().home, workspace_roots=(approved,))
    config.save()
    (outside / "build.log").write_bytes(b"INFO synthetic outside evidence\n" * 150)
    approved.rename(tmp_path / "parked")
    _directory_link(approved, outside)
    reads = _no_reads(monkeypatch)
    client = NoProvider()
    with pytest.raises(ValueError, match="outside_approved_workspace"):
        read_evidence_file(str(approved / "build.log"), "inspect", config.workspace_roots,
                           mode="shadow", client=client)
    with pytest.raises(RuntimeConfigError, match="workspace root changed"):
        RuntimeConfig.load()
    assert reads == [] and client.calls == [] and config.workspace_roots == (approved,)


@pytest.mark.parametrize("reverse", [False, True])
def test_stale_roots_do_not_disable_an_existing_approved_root(tmp_path, reverse):
    approved = tmp_path / "approved"
    approved.mkdir()
    path = approved / "build.log"
    raw = b"INFO local evidence\n"
    path.write_bytes(raw)
    nondirectory = tmp_path / "not-a-directory"
    nondirectory.write_text("not a root", encoding="utf-8")
    roots = [tmp_path / "unmounted", nondirectory, approved]
    if reverse:
        roots.reverse()
    result = read_evidence_file(str(path), "inspect", roots)
    assert result["output"] == raw.decode()
    assert result["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["source_path"] == str(path.resolve())


def test_only_stale_or_unrelated_roots_do_not_authorize_a_read(tmp_path, monkeypatch):
    path = tmp_path / "build.log"
    path.write_text("INFO local evidence\n", encoding="utf-8")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    reads = _no_reads(monkeypatch)
    with pytest.raises(ValueError, match="outside_approved"):
        read_evidence_file(str(path), "inspect", [tmp_path / "missing", unrelated])
    assert reads == []


def test_existing_parent_link_outside_roots_is_denied_before_read(tmp_path, monkeypatch):
    approved, outside = tmp_path / "approved", tmp_path / "outside"
    approved.mkdir()
    outside.mkdir()
    (outside / "build.log").write_text("INFO outside evidence\n", encoding="utf-8")
    _directory_link(approved / "linked", outside)
    reads = _no_reads(monkeypatch)
    with pytest.raises(ValueError, match="outside_approved"):
        read_evidence_file(str(approved / "linked" / "build.log"), "inspect", [approved])
    assert reads == []


@pytest.mark.parametrize("replace_root", [False, True])
def test_parent_replacement_between_validation_and_open_never_reads(tmp_path, monkeypatch, replace_root):
    approved, outside = tmp_path / "approved", tmp_path / "outside"
    approved.mkdir()
    outside.mkdir()
    parent = approved if replace_root else approved / "inner"
    if not replace_root:
        parent.mkdir()
    path = parent / "build.log"
    path.write_text("INFO inside evidence\n" * 150, encoding="utf-8")
    (outside / "build.log").write_text("INFO outside evidence\n" * 150, encoding="utf-8")
    replacement = tmp_path / "replacement"
    _directory_link(replacement, outside)
    original_open = evidence_file._open_descriptor
    swapped = []
    def race(resolved):
        parent.rename(tmp_path / "parked")
        replacement.rename(parent)
        swapped.append(True)
        return original_open(resolved)
    monkeypatch.setattr(evidence_file, "_open_descriptor", race)
    reads = _no_reads(monkeypatch)
    client = NoProvider()
    with pytest.raises((OSError, ValueError)):
        read_evidence_file(str(path), "inspect", [approved], mode="shadow", client=client)
    assert swapped == [True]
    assert reads == [] and client.calls == []


def test_final_file_replacement_between_validation_and_open_never_reads(tmp_path, monkeypatch):
    path, replacement = tmp_path / "build.log", tmp_path / "replacement.log"
    path.write_text("INFO original evidence\n", encoding="utf-8")
    replacement.write_text("INFO replaced evidence\n", encoding="utf-8")
    original_open = evidence_file._open_descriptor
    def race(resolved):
        path.rename(tmp_path / "parked.log")
        replacement.rename(path)
        return original_open(resolved)
    monkeypatch.setattr(evidence_file, "_open_descriptor", race)
    reads = _no_reads(monkeypatch)
    with pytest.raises(ValueError, match="evidence_source_changed"):
        read_evidence_file(str(path), "inspect", [tmp_path])
    assert reads == []


def test_actual_handle_path_is_required_before_read(tmp_path, monkeypatch):
    path = tmp_path / "build.log"
    path.write_text("INFO evidence\n", encoding="utf-8")
    def unavailable(_descriptor):
        raise ValueError("evidence_handle_path_unavailable")
    opened = []
    original_open = evidence_file._open_descriptor
    def capture(resolved):
        descriptor = original_open(resolved)
        opened.append(descriptor)
        return descriptor
    monkeypatch.setattr(evidence_file, "_open_descriptor", capture)
    monkeypatch.setattr(evidence_file, "_handle_path", unavailable)
    reads = _no_reads(monkeypatch)
    with pytest.raises(ValueError, match="handle_path_unavailable"):
        read_evidence_file(str(path), "inspect", [tmp_path])
    assert reads == []
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])  # Failed validation must release the opened handle.


def test_opened_handle_location_is_checked_even_for_same_file_identity(tmp_path, monkeypatch):
    approved, outside = tmp_path / "approved", tmp_path / "outside"
    approved.mkdir()
    outside.mkdir()
    path = approved / "build.log"
    path.write_bytes(b"INFO evidence\n")
    alias = outside / "build.log"
    try:
        os.link(path, alias)
    except OSError:
        pytest.skip("Hard links unavailable on this filesystem")
    original_open = evidence_file._open_descriptor
    monkeypatch.setattr(evidence_file, "_open_descriptor", lambda _path: original_open(alias))
    reads = _no_reads(monkeypatch)
    with pytest.raises(ValueError, match="outside_approved"):
        read_evidence_file(str(path), "inspect", [approved])
    assert reads == []


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path syntax")
def test_windows_extended_path_matches_canonical_handle(tmp_path):
    path = tmp_path / "build.log"
    path.write_bytes(b"INFO local evidence\n")
    result = read_evidence_file("\\\\?\\" + str(path), "inspect", ["\\\\?\\" + str(tmp_path)])
    assert result["output"] == "INFO local evidence\n"
    assert result["source_path"] == str(path.resolve())


def test_nonregular_source_is_rejected_without_read(tmp_path, monkeypatch):
    source = tmp_path / "source"
    if os.name == "posix":
        os.mkfifo(source)  # A blocking FIFO must never be read as a log.
    else:
        source.mkdir()
    reads = _no_reads(monkeypatch)
    with pytest.raises(ValueError, match="evidence_file_limit"):
        read_evidence_file(str(source), "inspect", [tmp_path])
    assert reads == []


def test_file_growth_after_open_is_rejected_before_read(tmp_path, monkeypatch):
    path = tmp_path / "build.log"
    path.write_bytes(b"small\n")
    original_open = evidence_file._open_descriptor
    def race(resolved):
        descriptor = original_open(resolved)
        path.write_bytes(b"x" * 32)
        return descriptor
    monkeypatch.setattr(evidence_file, "_open_descriptor", race)
    reads = _no_reads(monkeypatch)
    with pytest.raises(ValueError, match="evidence_file_limit"):
        evidence_file.read_evidence_bytes(path, [tmp_path], max_bytes=16)
    assert reads == []


def test_content_change_during_read_is_never_returned(tmp_path, monkeypatch):
    path = tmp_path / "build.log"
    path.write_bytes(b"INFO original\n")
    original_read = os.read
    changed = []
    def race(descriptor, limit):
        data = original_read(descriptor, limit)
        if not changed:
            path.write_bytes(b"INFO replacement is longer\n")
            changed.append(True)
        return data
    monkeypatch.setattr(evidence_file.os, "read", race)
    with pytest.raises(ValueError, match="evidence_source_changed"):
        read_evidence_file(str(path), "inspect", [tmp_path])
    assert changed == [True]


def test_exact_recovery_rejects_redirected_parent_before_read(tmp_path, monkeypatch):
    approved, outside = tmp_path / "approved", tmp_path / "outside"
    approved.mkdir()
    outside.mkdir()
    path = approved / "build.log"
    path.write_bytes(b"INFO identical bytes\n")
    (outside / "build.log").write_bytes(path.read_bytes())
    original = path.resolve()
    approved.rename(tmp_path / "parked")
    _directory_link(approved, outside)
    reads = _no_reads(monkeypatch)
    with pytest.raises(ValueError, match="evidence_source_changed"):
        evidence_file.read_evidence_bytes(original, [original.parent], max_bytes=1024, exact_path=True)
    assert reads == []
