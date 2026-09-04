# -*- coding: utf-8 -*-
"""「测试组」机制迁移与路由验证脚本

系统性移除旧「测试频道/测试群」概念后，测试隔离完全由固定转发组
「测试组」（id=test / name=测试组）实现。本脚本验证：

面板侧（磁盘迁移，main() 启动时执行，幂等）：
  1. 旧 is_test 标记的群/频道 → 并入「测试组」，且 is_test 字段从条目移除
  2. 旧 test_group_openid / test_channel_id 顶层键 → 并入「测试组」并删除
  3. 无 forwarding_groups 的旧配置 → 生成默认转发组（非测试实体）+ 测试组
  4. 已有 forwarding_groups → 仅补测试组、并入旧测试成员，其余组不动
  5. 重复执行结果不变（幂等）

bot 侧（plugins/config.py 内存归一化 + 路由）：
  6. 归一化始终保证「测试组」存在、id/name 固定
  7. get_test_group_openids 返回测试组成员
  8. get_groups_for_channel：频道/群同属一个转发组才路由；
     测试组内频道只路由到测试组内群，测试组与普通组之间无额外隔离
  9. 面板 save 归一化剥离 is_test、测试组常驻

运行：.venv/Scripts/python.exe 测试内容/test_test_group.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(detail)) if detail else ""))


# ---------- 面板侧迁移 ----------
saved_io = (sys.stdout, sys.stderr)
spec = importlib.util.spec_from_file_location(
    "wuppo_panel_testgroup", os.path.join(ROOT, "panel", "管理面板.pyw"))
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
sys.stdout, sys.stderr = saved_io

import plugins.config as config

pdir = tempfile.mkdtemp(prefix="wuppo_testgroup_")
orig_paths = (panel.SETTINGS_FILE, panel.SETTINGS_BAK,
              panel.DATA_DIR, panel.REGISTRATIONS_FILE)
panel.SETTINGS_FILE = os.path.join(pdir, "settings.json")
panel.SETTINGS_BAK = panel.SETTINGS_FILE + ".bak"
panel.DATA_DIR = pdir
panel.REGISTRATIONS_FILE = os.path.join(pdir, "qq_registrations.json")

TEST_ID = panel.TEST_FORWARDING_GROUP_ID
TEST_NAME = panel.TEST_FORWARDING_GROUP_NAME


def write_settings(data):
    with open(panel.SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_settings():
    with open(panel.SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def find_fg(fgs, gid):
    return next((f for f in fgs if str(f.get("id")) == gid), None)


try:
    # M1 旧配置：无 forwarding_groups，含 is_test 标记的群/频道 + 顶层旧键
    old = {
        "qq_group_openids": [
            {"openid": "G_TEST", "enabled": True, "name": "测试群", "is_test": True},
            {"openid": "G_PROD", "enabled": True, "name": "正式群"},
            {"openid": "G_OLD", "enabled": True, "name": "旧测试群"},
        ],
        "qq_user_openids": [],
        "discord_channels": [
            {"id": "C_TEST", "name": "测试频道", "enabled": True, "is_test": True},
            {"id": "C_PROD", "name": "正式频道", "enabled": True},
            {"id": "C_OLD", "name": "旧测试频道"},
        ],
        "test_group_openid": "G_OLD",
        "test_channel_id": "C_OLD",
        "backfill_enabled": True,
        "backfill_limit": 10,
    }
    write_settings(old)
    panel.migrate_forwarding_groups()
    data = read_settings()

    fgs = data.get("forwarding_groups")
    check("M1 生成默认转发组 + 测试组", isinstance(fgs, list) and len(fgs) == 2, fgs)
    tg = find_fg(fgs, TEST_ID)
    check("M1 测试组 id/name 固定", tg is not None
          and tg["name"] == TEST_NAME, tg)
    check("M1 测试组并入旧测试成员（is_test + 顶层旧键）",
          set(tg["groups"]) == {"G_TEST", "G_OLD"}
          and set(tg["channels"]) == {"C_TEST", "C_OLD"}, tg)
    dg = find_fg(fgs, panel.FORWARDING_GROUP_DEFAULT_ID)
    check("M1 默认组只含非测试实体",
          set(dg["groups"]) == {"G_PROD"}
          and set(dg["channels"]) == {"C_PROD"}, dg)
    check("M1 群/频道条目已剥离 is_test",
          all("is_test" not in g for g in data["qq_group_openids"])
          and all("is_test" not in c for c in data["discord_channels"]))
    check("M1 顶层旧测试键已删除",
          "test_group_openid" not in data and "test_channel_id" not in data)

    # M2 幂等：再次迁移，结果不变
    snapshot = json.dumps(read_settings(), sort_keys=True, ensure_ascii=False)
    panel.migrate_forwarding_groups()
    check("M2 重复迁移结果不变（幂等）",
          json.dumps(read_settings(), sort_keys=True, ensure_ascii=False) == snapshot)

    # M3 已有转发组：只补测试组并并入旧测试成员，其余组不动
    existing = {
        "qq_group_openids": [
            {"openid": "G1", "enabled": True, "name": "群1"},
            {"openid": "G_T", "enabled": True, "name": "测试群", "is_test": True},
        ],
        "qq_user_openids": [],
        "discord_channels": [
            {"id": "C1", "enabled": True, "name": "频道1"},
            {"id": "C_T", "enabled": True, "name": "测试频道", "is_test": True},
        ],
        "forwarding_groups": [
            {"id": "fg1", "name": "转发组1",
             "channels": ["C1"], "groups": ["G1"]},
        ],
    }
    write_settings(existing)
    panel.migrate_forwarding_groups()
    data = read_settings()
    fgs = data["forwarding_groups"]
    check("M3 已有转发组保留 + 补测试组", len(fgs) == 2
          and find_fg(fgs, "fg1")["name"] == "转发组1", fgs)
    tg = find_fg(fgs, TEST_ID)
    check("M3 测试组并入旧 is_test 成员",
          set(tg["groups"]) == {"G_T"} and set(tg["channels"]) == {"C_T"}, tg)

    # M4 面板 save：剥离 is_test、测试组常驻（即使成员为空）
    empty = {
        "qq_group_openids": [],
        "qq_user_openids": [],
        "discord_channels": [],
        "forwarding_groups": [
            {"id": TEST_ID, "name": TEST_NAME, "channels": [], "groups": []},
        ],
    }
    panel.save_settings(empty)
    on_disk = read_settings()
    check("M4 保存后测试组常驻", len(on_disk["forwarding_groups"]) == 1
          and on_disk["forwarding_groups"][0]["id"] == TEST_ID, on_disk)

    # M5 save 归一化：缺测试组时自动补上，is_test 被剥离
    save_case = {
        "qq_group_openids": [{"openid": "G1", "enabled": True, "is_test": True}],
        "qq_user_openids": [],
        "discord_channels": [],
        "forwarding_groups": [
            {"id": "fg9", "name": "转发组9", "channels": [], "groups": ["G1"]},
        ],
    }
    panel.save_settings(save_case)
    on_disk = read_settings()
    check("M5 save 自动补测试组且 id/name 固定",
          find_fg(on_disk["forwarding_groups"], TEST_ID) is not None
          and find_fg(on_disk["forwarding_groups"], TEST_ID)["name"] == TEST_NAME,
          on_disk["forwarding_groups"])
    check("M5 保存后条目剥离 is_test",
          all("is_test" not in g for g in on_disk["qq_group_openids"]))

    # M6 校验：缺测试组 / 测试组改名 / 超过上限 → 拒绝
    too_many = [
        {"id": TEST_ID, "name": TEST_NAME, "channels": [], "groups": []},
    ]
    for i in range(10):
        too_many.append(
            {"id": "x%d" % i, "name": "g%d" % i, "channels": [], "groups": []})
    bad_fgs = [
        ([{"id": "fg1", "name": "转发组1", "channels": [], "groups": []}],
         "缺测试组"),
        ([{"id": TEST_ID, "name": "改名了", "channels": [], "groups": []}],
         "测试组改名"),
        (too_many, "超过10个"),
    ]
    for fgs, label in bad_fgs:
        err = panel._validate_settings({
            "qq_group_openids": [], "qq_user_openids": [],
            "discord_channels": [], "forwarding_groups": fgs,
        })
        check("M6 校验拒绝: " + label, err is not None, err)
    ok_err = panel._validate_settings({
        "qq_group_openids": [], "qq_user_openids": [],
        "discord_channels": [],
        "forwarding_groups": [
            {"id": "fg1", "name": "转发组1", "channels": [], "groups": []},
            {"id": TEST_ID, "name": TEST_NAME, "channels": [], "groups": []},
        ],
    })
    check("M6 合法载荷通过", ok_err is None, ok_err)

    # ---------- bot 侧内存归一化 + 路由 ----------
    # 用独立临时 settings 文件隔离，避免污染真实配置
    cdir = tempfile.mkdtemp(dir=pdir)
    cfile = os.path.join(cdir, "settings.json")
    orig = (config.SETTINGS_FILE, config._settings_cache,
            config._settings_cache_mtime, config._settings_cache_size)

    def reset(path):
        config.SETTINGS_FILE = path
        config._settings_cache = None
        config._settings_cache_mtime = None
        config._settings_cache_size = None

    reset(cfile)
    with open(cfile, "w", encoding="utf-8") as f:
        json.dump({
            "qq_group_openids": [
                {"openid": "G_TEST", "enabled": True, "is_test": True},
                {"openid": "G_PROD", "enabled": True},
            ],
            "qq_user_openids": [],
            "discord_channels": [
                {"id": "C_TEST", "enabled": True, "is_test": True},
                {"id": "C_PROD", "enabled": True},
            ],
        }, f)

    s = config._load_settings()
    fgs = s["forwarding_groups"]
    check("B1 归一化生成默认组 + 测试组", len(fgs) == 2, fgs)
    tg = find_fg(fgs, config.TEST_FORWARDING_GROUP_ID)
    check("B1 测试组 id/name 固定", tg is not None
          and tg["name"] == config.TEST_FORWARDING_GROUP_NAME, tg)
    check("B1 旧 is_test 并入测试组",
          set(tg["groups"]) == {"G_TEST"} and set(tg["channels"]) == {"C_TEST"}, tg)
    check("B1 条目 is_test 已剥离（内存）",
          all("is_test" not in g for g in s["qq_group_openids"])
          and all("is_test" not in c for c in s["discord_channels"]))

    check("B2 get_test_group_openids 返回测试组成员",
          config.get_test_group_openids() == {"G_TEST"})

    # B3 路由：测试组内频道 → 只路由到测试组内群
    targets = config.get_groups_for_channel("C_TEST")
    check("B3 测试频道只路由到测试群", targets == ["G_TEST"], targets)
    # 普通组频道 → 普通群
    targets = config.get_groups_for_channel("C_PROD")
    check("B3 普通频道只路由到普通群", targets == ["G_PROD"], targets)
    # 未启用频道不路由
    with open(cfile, "w", encoding="utf-8") as f:
        json.dump({
            "qq_group_openids": [{"openid": "G_PROD", "enabled": True}],
            "qq_user_openids": [],
            "discord_channels": [{"id": "C_PROD", "enabled": False}],
        }, f)
    reset(cfile)
    check("B3 未启用频道不路由", config.get_groups_for_channel("C_PROD") == [])

    # B4 归一化幂等（同一内存数据再次归一化不改变测试组）
    before = json.dumps(s["forwarding_groups"], sort_keys=True)
    config._ensure_forwarding_groups(s)
    check("B4 归一化幂等", json.dumps(s["forwarding_groups"], sort_keys=True) == before)
    reset(cfile)

    # 清理 bot 侧缓存指向
    (config.SETTINGS_FILE, config._settings_cache,
     config._settings_cache_mtime, config._settings_cache_size) = orig
    shutil.rmtree(cdir, ignore_errors=True)

finally:
    (panel.SETTINGS_FILE, panel.SETTINGS_BAK,
     panel.DATA_DIR, panel.REGISTRATIONS_FILE) = orig_paths
    shutil.rmtree(pdir, ignore_errors=True)

print()
failed = [x for x in results if not x[1]]
print("总计 %d 项，通过 %d 项，失败 %d 项"
      % (len(results), len(results) - len(failed), len(failed)))
if failed:
    for name, _, detail in failed:
        print("FAIL: " + name + " | " + str(detail))
    sys.exit(1)
print("全部通过")
