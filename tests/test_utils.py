"""工具层测试：JSON 读写、消息解析、HTTP 客户端、路径解析。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from nonebot_plugin_genshin_role_info import paths
from nonebot_plugin_genshin_role_info.data_source.draw_role_card import (
    MissingIconSourceError,
    get_icon_url,
)
from nonebot_plugin_genshin_role_info.utils.http_utils import AsyncHttpx
from nonebot_plugin_genshin_role_info.utils.json_utils import (
    get_message_at,
    load_json,
    save_json,
)


# ---------------------------------------------------------------------------
# JSON 读写
# ---------------------------------------------------------------------------
def test_load_json_missing_file_creates_empty(tmp_path: Path):
    """读取不存在的文件应创建空 JSON 并返回空 dict（与源实现一致）。"""
    target = tmp_path / "sub" / "missing.json"
    assert load_json(target) == {}
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {}


def test_save_and_load_roundtrip_keeps_unicode(tmp_path: Path):
    target = tmp_path / "data.json"
    payload = {"角色": "胡桃", "别名": ["雪霁梅香"], "分数": 184.4}
    save_json(payload, target)
    # ensure_ascii=False：中文必须以原字符写入，便于人工排查
    raw = target.read_text(encoding="utf-8")
    assert "胡桃" in raw
    assert load_json(target) == payload


def test_save_json_creates_parent_dirs(tmp_path: Path):
    target = tmp_path / "a" / "b" / "c.json"
    save_json([1, 2, 3], target)
    assert load_json(target) == [1, 2, 3]


def test_get_message_at_from_event_json():
    event = {
        "message": [
            {"type": "text", "data": {"text": "甘雨面板"}},
            {"type": "at", "data": {"qq": "10001"}},
            {"type": "at", "data": {"qq": "10002"}},
        ]
    }
    assert get_message_at(json.dumps(event)) == [10001, 10002]


def test_get_message_at_without_at_returns_empty():
    assert get_message_at(json.dumps({"message": [{"type": "text", "data": {}}]})) == []
    assert get_message_at(json.dumps({})) == []


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------
def test_resource_paths_exist():
    """集中式路径必须指向真实存在的内置资源。"""
    assert Path(paths.RES_PATH).is_dir()
    assert Path(paths.json_path).is_dir()
    assert Path(paths.bg_path).is_dir()
    assert Path(paths.other_path).is_dir()
    assert Path(paths.font_path).is_dir()
    assert Path(paths.regoin_path).is_dir()
    assert Path(paths.outline_path).is_dir()


def test_json_dir_is_under_res():
    """json_path 必须位于 res/ 之下（render.py 曾经在此算错一级）。"""
    assert Path(paths.json_path).parent == Path(paths.RES_PATH)
    assert Path(paths.other_path).parent == Path(paths.RES_PATH)
    assert Path(paths.font_path).parent == Path(paths.RES_PATH)


def test_required_data_files_present():
    required = [
        "role_info.json",
        "score.json",
        "artifact.json",
        "convert.json",
        "locs.json",
        "miao_damage_rules.json",
        "miao_damage_talents.json",
        "miao_damage_equipment.json",
    ]
    for name in required:
        file = Path(paths.json_path) / name
        assert file.is_file(), f"缺少数据文件 {name}"
        assert file.stat().st_size > 100, f"{name} 内容异常"


def test_required_fonts_present():
    for name in ("hywh.ttf", "number.ttf", "优设标题黑.ttf", "tahomabd.ttf"):
        assert (Path(paths.font_path) / name).is_file(), f"缺少字体 {name}"


def test_damage_render_assets_present():
    """伤害表渲染依赖的遮罩素材必须存在。"""
    for name in ("遮罩top.png", "遮罩body.png", "遮罩bottom.png"):
        assert (Path(paths.other_path) / name).is_file(), f"缺少素材 {name}"


def test_data_dir_from_env(monkeypatch):
    """数据目录可通过环境变量重定向。"""
    monkeypatch.setenv("GENSHIN_ROLE_INFO_DATA_DIR", "/tmp/gri_redirect_check")
    assert paths.get_data_path() == Path("/tmp/gri_redirect_check")


def test_data_dir_fallback(monkeypatch):
    monkeypatch.delenv("GENSHIN_ROLE_INFO_DATA_DIR", raising=False)
    assert paths.get_data_path() == Path(paths.BASE_PATH) / "user_data"


def test_ensure_data_dirs_creates_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("GENSHIN_ROLE_INFO_DATA_DIR", str(tmp_path / "d"))
    # ensure_data_dirs 使用模块常量，这里直接验证其创建的子目录结构
    created = paths.ensure_data_dirs()
    assert created.is_dir()
    for sub in ("player_info", "group_info", "qq_logo", "res/character", "res/weapon"):
        assert (created / sub).is_dir(), f"缺少 {sub}"


# ---------------------------------------------------------------------------
# 图标 URL 解析
# ---------------------------------------------------------------------------
def test_get_icon_url_prefers_source():
    assert get_icon_url("UI_X", "https://cdn/a.png", "https://enka/{}.png") == "https://cdn/a.png"


def test_get_icon_url_uses_fallback_for_asset_name():
    assert (
        get_icon_url("UI_RelicIcon_15006_4", "", "https://enka.network/ui/{}.png")
        == "https://enka.network/ui/UI_RelicIcon_15006_4.png"
    )


def test_get_icon_url_raises_for_mihoyo_hash_without_source():
    """32 位十六进制是米游社资源哈希，缺少原始链接时必须显式报错。"""
    with pytest.raises(MissingIconSourceError):
        get_icon_url("a" * 32, "", "https://enka.network/ui/{}.png")


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------
async def test_get_content_success(monkeypatch):
    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, content=b"hello", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await AsyncHttpx.get_content("https://example.com/x") == b"hello"


async def test_get_content_raises_on_500(monkeypatch):
    calls = {"n": 0}

    async def fake_get(self, url, **kwargs):
        calls["n"] += 1
        return httpx.Response(500, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with pytest.raises(httpx.HTTPStatusError):
        await AsyncHttpx.get_content("https://example.com/x", retry_interval=0)
    # 5xx 会重试，但次数固定（不多不少 3 次）
    assert calls["n"] == 3


async def test_get_content_does_not_retry_4xx(monkeypatch):
    calls = {"n": 0}

    async def fake_get(self, url, **kwargs):
        calls["n"] += 1
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with pytest.raises(httpx.HTTPStatusError):
        await AsyncHttpx.get_content("https://example.com/x", retry_interval=0)
    # 4xx 是确定性错误，不应重试
    assert calls["n"] == 1


async def test_download_file_returns_false_on_404(monkeypatch, tmp_path: Path):
    class FakeStream:
        async def __aenter__(self):
            return httpx.Response(
                404, request=httpx.Request("GET", "https://example.com/x")
            )

        async def __aexit__(self, *exc):
            return False

    def fake_stream(self, method, url, **kwargs):
        return FakeStream()

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    target = tmp_path / "out.bin"
    assert await AsyncHttpx.download_file("https://example.com/x", target) is False
    assert not target.exists()


async def test_download_file_success(monkeypatch, tmp_path: Path):
    class FakeStream:
        async def __aenter__(self):
            return httpx.Response(
                200,
                content=b"binary-data",
                request=httpx.Request("GET", "https://example.com/x"),
            )

        async def __aexit__(self, *exc):
            return False

    def fake_stream(self, method, url, **kwargs):
        return FakeStream()

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    target = tmp_path / "nested" / "out.bin"
    assert await AsyncHttpx.download_file("https://example.com/x", target) is True
    assert target.read_bytes() == b"binary-data"


async def test_download_file_tries_candidate_urls(monkeypatch, tmp_path: Path):
    """候选地址列表应被依次尝试。"""
    attempts: list[str] = []

    class FakeStream:
        def __init__(self, url):
            self.url = url

        async def __aenter__(self):
            status = 200 if self.url.endswith("good") else 404
            return httpx.Response(
                status,
                content=b"ok" if status == 200 else b"",
                request=httpx.Request("GET", self.url),
            )

        async def __aexit__(self, *exc):
            return False

    def fake_stream(self, method, url, **kwargs):
        attempts.append(url)
        return FakeStream(url)

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    target = tmp_path / "out.bin"
    ok = await AsyncHttpx.download_file(
        ["https://example.com/bad", "https://example.com/good"],
        target,
        retry_interval=0,
    )
    assert ok is True
    assert target.read_bytes() == b"ok"
    assert attempts == ["https://example.com/bad", "https://example.com/good"]


def test_validate_image(tmp_path: Path):
    from PIL import Image

    from nonebot_plugin_genshin_role_info.utils.download_utils import (
        validate_image,
        validate_nonempty,
        validate_utf8_text,
    )

    good = tmp_path / "good.png"
    Image.new("RGB", (8, 8), "red").save(good)
    assert validate_image(good) is True

    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    assert validate_image(bad) is False

    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    assert validate_nonempty(empty) is False

    text = tmp_path / "a.txt"
    text.write_text("hello", encoding="utf-8")
    assert validate_utf8_text(text) is True
    text.write_text("   ", encoding="utf-8")
    assert validate_utf8_text(text) is False
