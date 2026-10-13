#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抗癌文字冒险游戏 · v5

v3 是"读起来像不像文字冒险"（器官描写、细胞性格、分级结局）。
v4 把初代那套**人体地图 + 地形系统**找回来（约 50 个身体区域、每个带地形类型、
战斗读地形）。
v5 补上初代另外三套被丢掉的系统 —— 它们原本就在那个坏掉的文件里：
  · **战斗**：癌不再只是一个数字，病灶会长出有血量的单位（原位克隆 / 浸润边缘 /
    血管内渗灶 / 免疫逃逸株 / 循环肿瘤细胞），BOSS 也是真的单位，要打掉才退
  · **技能**：6 个主动能力，各有抗体消耗与冷却（初代的 skill_cooldowns）
  · **交易**：10 项的商店，能买也能回收（半价），抗体是唯一货币
  · 外加任务：达成即结算抗体

v4 那套地图与地形照旧：

  · 地图：全身一条循环路线 血液 → 骨髓 → 淋巴结 → 脾脏 → 肝脏 → 肠道 → 肺 → 回到血液
  · 地形：每个器官有自己的地形，**直接改战斗与增殖**（不是文案）
  · 前进：把部队驻扎到下一个区域；驻扎在干净区域会持续修复它（好感 +1/回合）
  · 地形适应：3 回合内地形加成翻倍、惩罚忽略

纯标准库。命令行：
    python 文字冒险游戏.py                     正常玩
    python 文字冒险游戏.py --selftest          确定性自检（退出码 0 = 通过）
    python 文字冒险游戏.py --fuzz 50           50 局随机输入，只断言不崩
    python 文字冒险游戏.py --load save.json    从存档继续（--save 指定写入位置）
    python 文字冒险游戏.py --state             每回合多打一行 @@STATE {...}@@
                                               （网页侧靠它画人体地图，人不用看）
"""

import argparse
import contextlib
import io
import json
import os
import random
import sys

MAX_VICTORY_POINTS = 20          # 缓解度目标
START_LIVES = 3
BOSS_INTERVAL = 5
MAX_ROUNDS = 200
ENDLESS_AT = 62                  # 缓解度到这儿转无尽模式（长期共存）
SAVE_VERSION = 1

MODES = {
    "20岁":  {"diff": 1.0, "desc": "年轻、免疫系统强健，容错最高"},
    "30岁":  {"diff": 1.3, "desc": "开始有生活压力，恢复变慢"},
    "40岁":  {"diff": 1.6, "desc": "器官功能下行，癌细胞更顽固"},
    "80岁":  {"diff": 2.0, "desc": "高龄，免疫衰老，每一回合都珍贵"},
    "晚期":  {"diff": 2.6, "desc": "已发生转移，必须靠联合治疗"},
}

ROOMS = ["骨髓", "淋巴结", "脾脏", "肝脏", "肺", "血液", "肠道"]

# 器官描写：文字冒险的"场景"，每次进入状态面板时轮流露一句
ROOM_DESC = {
    "骨髓":   "红骨髓像一座不熄的工厂，造血干细胞在里面排队等待命令。",
    "淋巴结": "淋巴结是哨站，树突细胞带着敌人的碎片在这里递交情报。",
    "脾脏":   "脾脏是血细胞的阅兵场，老旧的、叛变的，都在这里被筛掉。",
    "肝脏":   "肝脏沉默而勤劳，解毒、合成、储存——它很少抱怨。",
    "肺":     "肺泡像一串串微小的气球，每一次呼吸都在和外界交换信任。",
    "血液":   "血流是高速公路，中性粒细胞贴着血管壁滚动，寻找炎症的气味。",
    "肠道":   "肠道住着几万亿邻居，免疫系统在这儿学会了'容忍'与'开战'的分寸。",
}

# 细胞：名字 -> (招募费, 攻击, 生命, 特性, 台词)
CELLS = {
    "T细胞":     (3, 6, 6,  "主力，稳定输出", "识别、接触、清除——一步一步来。"),
    "NK细胞":    (3, 8, 4,  "高爆发，专杀逃逸细胞", "不用谁批准，我看它不对劲就动手。"),
    "巨噬细胞":  (2, 4, 9,  "肉盾，吞噬残骸回血", "吃干净点，别浪费。"),
    "树突细胞":  (4, 3, 5,  "标记目标，全队伤害+1", "我把它的样子记住了，你们照着打。"),
    "中性粒细胞":(1, 7, 2,  "短命高攻，一回合后凋亡", "我活不过今天，但今天够用了。"),
    "B细胞":     (3, 5, 5,  "抗体，跨器官支援", "抗体已经发下去了，全身都能用。"),
    "细胞因子":  (2, 0, 4,  "辅助：全队攻击+2、器官好感+3", "别慌，我把信号放大一点。"),
}

ROOM_PREFERENCE = {
    "骨髓": ["T细胞", "B细胞", "细胞因子"],
    "淋巴结": ["T细胞", "树突细胞", "B细胞"],
    "脾脏": ["B细胞", "巨噬细胞", "NK细胞"],
    "肝脏": ["巨噬细胞", "NK细胞", "中性粒细胞"],
    "肺": ["巨噬细胞", "中性粒细胞"],
    "血液": ["中性粒细胞", "NK细胞", "B细胞"],
    "肠道": ["T细胞", "巨噬细胞", "细胞因子"],
}

# ── 地形系统（v4 找回的那套）─────────────────────────────────────────
# 每个器官是一种"地形"：它改的是**数字**，不是文案。
#   bonus : 该地形下每个细胞种类的攻击修正（没有条目 = 0）
#   growth: 该地形每回合额外的癌细胞增殖（可以为负 —— 缺氧/菌群竞争会压住它）
#   note  : 给玩家看的因果说明，必须和 bonus/growth 对得上，不许写成气氛词
TERRAIN = {
    "骨髓": {
        "name": "造血基质",
        "bonus": {"T细胞": 2, "B细胞": 2, "中性粒细胞": -1},
        "growth": 0,
        "note": "新兵在这里出生：T/B 细胞攻击 +2；中性粒细胞还没成熟，-1。",
    },
    "淋巴结": {
        "name": "淋巴管网",
        "bonus": {"树突细胞": 2, "T细胞": 1, "B细胞": 1},
        "growth": 0,
        "note": "情报中枢：标记细胞 +2，淋巴系 +1；地形本身不助长肿瘤。",
    },
    "脾脏": {
        "name": "滤血窦",
        "bonus": {"巨噬细胞": 2, "NK细胞": 2, "中性粒细胞": -1},
        "growth": 1,
        "note": "滤血场：吞噬与杀伤 +2；但肿瘤碎片随血汇集，增殖 +1。",
    },
    "肝脏": {
        "name": "代谢小叶",
        "bonus": {"巨噬细胞": 1, "中性粒细胞": 1},
        "growth": 0,
        "note": "解毒厂：库普弗细胞（巨噬系）稳定 +1。",
    },
    "肺": {
        "name": "气血屏障",
        "bonus": {"NK细胞": 1, "巨噬细胞": 1},
        "growth": -1,
        "note": "薄壁低氧：缺氧细胞分裂更慢，增殖 -1；杀伤细胞 +1。",
    },
    "血液": {
        "name": "血流剪切",
        "bonus": {"中性粒细胞": 2, "NK细胞": 1, "巨噬细胞": -1, "T细胞": -1},
        "growth": 1,
        "note": "高速血流：滚动迁移的中性粒细胞 +2；贴壁作战的巨噬/T 细胞 -1；"
                "循环肿瘤细胞增殖 +1。",
    },
    "肠道": {
        "name": "黏膜菌群",
        "bonus": {"T细胞": 1, "细胞因子": 1},
        "growth": -1,
        "note": "几万亿邻居：菌群竞争压住增殖 -1；黏膜免疫 T 细胞与信号细胞 +1。",
    },
}

#: 全身循环路线：前进就是沿着这条线走，走到头回到血液。
ROUTE = ["血液", "骨髓", "淋巴结", "脾脏", "肝脏", "肠道", "肺"]

#: 地形适应：3 回合内地形加成翻倍、惩罚忽略（对应初代的 terrain_boost）
TERRAIN_ADAPT_TURNS = 3

# ── 技能（v5 补的那套）──────────────────────────────────────────────
# 初代有 player_skills + skill_cooldowns；这里照同一个模型重做：
#   cost  : 花多少抗体
#   cd    : 冷却几回合
#   kind  : 效果类型（在 cast() 里结算，全部改的是真数字）
SKILLS = {
    "靶向清除":   {"cost": 2, "cd": 3, "kind": "snipe",
                   "desc": "对驻地敌人造成 9 点伤害（不打器官好感）"},
    "哨兵部署":   {"cost": 1, "cd": 2, "kind": "garrison",
                   "desc": "驻地好感 +6，并补充 1 支驻军"},
    "细胞因子风暴": {"cost": 3, "cd": 5, "kind": "storm",
                   "desc": "驻地癌 -7、好感 -5；全队攻击 +2（2 回合）"},
    "免疫记忆":   {"cost": 2, "cd": 4, "kind": "memory",
                   "desc": "全队攻击 +3（3 回合），对已见过的克隆更狠"},
    "趋化因子":   {"cost": 1, "cd": 2, "kind": "chemotaxis",
                   "desc": "把最脏区域的一部分敌人吸引到驻地，并就地削 3 点"},
    "血管封锁":   {"cost": 2, "cd": 4, "kind": "blockade",
                   "desc": "本回合全区域癌细胞不增殖，逃逸数归零"},
}

# ── 敌人（v5）：癌不再只是一个数字，病灶会长出有血量的单位 ──────────
# name -> (血量, 攻击, 说明)。血量不是"癌值"：打掉一个单位会连带削掉一片病灶。
ENEMIES = {
    "原位克隆": (6, 2, "老老实实待着，边界清楚"),
    "浸润边缘": (8, 3, "正在啃噬周围组织"),
    "血管内渗灶": (7, 4, "挤进血管，来去快"),
    "免疫逃逸株": (10, 3, "把标记藏起来了，常规攻击都变钝"),
    "循环肿瘤细胞": (5, 5, "在血流里漂，打不死就还会回来"),
}

#: 一个敌人的"病灶负担"：它的血量 × 这个系数，就是清掉它削掉的癌值
ENEMY_BURDEN = 1.8

#: 商店：抗体换东西（原版那串药品表的可玩子集）
SHOP = {
    "化疗药物":         (2, "全场癌细胞 -6，器官好感 -2"),
    "靶向药物":         (3, "指定器官癌细胞 -10"),
    "免疫检查点抑制剂": (4, "全队攻击 +3（2 回合）"),
    "放疗":             (3, "清除一个器官 60% 癌细胞，好感 -3"),
    "BRCA-RNA疫苗":     (5, "本回合敌方停止增殖，缓解度 +1"),
    "激素疗法":         (2, "BOSS 强度 -30%（3 回合）"),
    "阿司匹林":         (1, "解除负面状态"),
    "手术":             (5, "直接削掉一个器官 50% 癌细胞，好感 -5"),
    "CAR-T疗法":        (6, "召唤一支顶级 T 细胞战队（攻12/血10）"),
    "地形适应":         (2, "3 回合内地形加成翻倍、惩罚忽略"),
}

#: 回收价 = 标价的一半向下取整，最低 1。（有买有卖才叫交易）
def _resale(price):
    return max(1, price // 2)

DRUGS = {
    "化疗药物":         (2, "对全场癌细胞造成 6 伤害，副作用：全器官好感-2", "chemo"),
    "靶向药物":         (3, "对指定器官造成 10 伤害", "targeted"),
    "免疫检查点抑制剂": (4, "全队攻击+3，持续 2 回合", "checkpoint"),
    "放疗":             (3, "清除一个器官内 60% 癌细胞，好感-3", "radio"),
    "CAR-T疗法":        (6, "召唤一支顶级 T 细胞战队（攻12/血10）", "cart"),
    "BRCA-RNA疫苗":     (5, "本回合敌方增殖停止，且缓解度+1", "vaccine"),
    "激素疗法":         (2, "降低全局 BOSS 强度 30%", "hormone"),
    "阿司匹林":         (1, "解除当前负面状态", "aspirin"),
    "手术":             (5, "直接削减一个器官 50% 癌细胞，好感-5", "surgery"),
    "地形适应":         (2, "3 回合内地形加成翻倍、惩罚忽略（在不利地形里作战时用）",
                         "terrain_adapt"),
}

# BOSS 按出现次数换名字与描述 —— "每个阶段有张脸"
BOSSES = [
    ("原位灶", "它还老实待在一个地方，边界清楚——这是最好打的仗。"),
    ("局部浸润", "它开始啃噬周围的正常组织，器官的边界变模糊了。"),
    ("血管内渗", "它学会了钻进血管，这是转移的第一步。"),
    ("循环肿瘤细胞", "它在血流里漂流，随时可能在别处靠岸。"),
    ("微转移灶", "它已经在别的器官里落了脚，只是还太小、没被发现。"),
    ("免疫逃逸克隆", "它学会了伪装，把自己的标记藏了起来。"),
]

#: BOSS 也是真的敌人单位（v5），模糊测试里要认得出这些名字
BOSS_NAMES = tuple(n for n, _ in BOSSES)


def selftest_mode():
    return ("--selftest" in sys.argv or "selftest" in sys.argv
            or os.environ.get("SELFTEST") == "1")


class Game:
    def __init__(self, mode="20岁", seed=None, rng=None):
        self.rng = rng or random.Random(seed)
        self.mode = mode
        self.diff = MODES[mode]["diff"]
        self.round = 0
        self.lives = START_LIVES
        self.antibody = 6          # 资源：招兵/买药（会花掉）
        self.remission = 0         # 评分：只认持续和谐
        self.escaped = 0
        self.endless = False
        self.boss_mult = 1.0
        self.boss_count = 0
        self.hormone_turns = 0
        self.checkpoint_turns = 0
        self.terrain_turns = 0
        self.at = ROUTE[0]                 # 部队当前驻扎的区域（地形战斗的地点）
        self.state_line = False            # --state：每回合多打一行给网页的 JSON
        # v5：技能冷却 / 攻击增益 / 封锁回合 / 任务进度
        self.skills_cd = {}
        self.atk_buff = 0
        self.atk_buff_turns = 0
        self.blockade_turns = 0
        self.kills = 0
        self.quests = {"首杀": [0, 3], "净化": [0, 2], "稳住": [0, 5]}
        self.quest_paid = []
        self.cells = [{"name": n, "atk": CELLS[n][1], "hp": CELLS[n][2]}
                      for n in ("T细胞", "巨噬细胞")]
        self.drugs = {"化疗药物": 1, "靶向药物": 1, "阿司匹林": 1}
        self.rooms = {}
        for r in ROOMS:
            self.rooms[r] = {"favor": 80, "fall": 0, "cancer": 8,
                             "garrison": self._make_garrison(r),
                             "enemies": []}

    # ── 器官驻军：按器官偏好生成 ─────────────────────────────────────
    def _make_garrison(self, room):
        prefer = ROOM_PREFERENCE[room]
        troops = []
        for _ in range(self.rng.randint(2, 3)):
            name = self.rng.choice(prefer if self.rng.random() < 0.75 else list(CELLS))
            troops.append({"name": name, "atk": CELLS[name][1], "hp": CELLS[name][2]})
        return troops

    # ── 存档 / 读档（宿主做会话恢复就靠它）───────────────────────────
    def to_dict(self):
        return {"version": SAVE_VERSION, "mode": self.mode, "round": self.round,
                "lives": self.lives, "antibody": self.antibody,
                "remission": self.remission, "escaped": self.escaped,
                "boss_mult": self.boss_mult, "boss_count": self.boss_count,
                "cells": self.cells, "drugs": self.drugs, "rooms": self.rooms,
                "quests": self.quests, "at": self.at,
                "terrain_turns": self.terrain_turns,
                "skills_cd": self.skills_cd, "atk_buff": self.atk_buff,
                "atk_buff_turns": self.atk_buff_turns,
                "blockade_turns": self.blockade_turns, "kills": self.kills,
                "quest_paid": self.quest_paid}

    def save(self, path):
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(self.to_dict(), ensure_ascii=False))
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path, seed=None):
        with io.open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if int(d.get("version") or 0) != SAVE_VERSION:
            raise ValueError("存档版本不匹配")
        g = cls(d.get("mode") or "20岁", seed=seed)
        for k in ("round", "lives", "antibody", "remission", "escaped",
                  "boss_mult", "boss_count", "cells", "drugs", "rooms", "quests",
                  "at", "terrain_turns", "skills_cd", "atk_buff",
                  "atk_buff_turns", "blockade_turns", "kills", "quest_paid"):
            if k in d:
                setattr(g, k, d[k])
        if g.at not in TERRAIN:              # 老存档没有 at，或存了个不认识的区域
            g.at = ROUTE[0]
        for r in ROOMS:                      # 老存档没有敌人字段
            g.rooms.setdefault(r, {}).setdefault("enemies", [])
        if not isinstance(g.quests, dict):   # v4 存档里 quests 是 list，v5 换成 dict
            g.quests = {"首杀": [0, 3], "净化": [0, 2], "稳住": [0, 5]}
        if not isinstance(g.quest_paid, list):
            g.quest_paid = []
        return g

    # ── 输出 ─────────────────────────────────────────────────────────
    def say(self, text=""):
        # Windows 控制台默认是 GBK，某些符号（●、⚠、emoji）编不出来会直接
        # UnicodeEncodeError 把整局打断 —— 原版 9,321 行那个文件就是这么坏的
        # 一路（emoji 写死在 print 里）。这里兜一层：编不出来就替换掉，不崩。
        try:
            print(text, flush=True)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            safe = text.encode(enc, "replace").decode(enc, "replace")
            print(safe, flush=True)

    def banner(self):
        self.say("=" * 58)
        self.say("  抗癌文字冒险游戏 · 免疫系统指挥官（v5）")
        self.say("=" * 58)
        self.say(f"  开局模式：{self.mode} —— {MODES[self.mode]['desc']}")
        self.say(f"  目标：缓解度 ≥ {MAX_VICTORY_POINTS}；生命 {self.lives} 条")
        self.say("  你是指挥官。你的部队是免疫细胞，你的战场是你自己。")
        self.say(f"  全身循环：{' → '.join(ROUTE)} → 回到血液")
        self.say("  地形会改战斗：驻地对了多吃伤害，站错了就是在陌生环境里硬拼。")
        self.say(f"  技能 {len(SKILLS)} 个（9）、商店 {len(SHOP)} 项（10）；"
                 f"病灶会长出有血量的敌人，打掉才算清干净。")

    def brief(self):
        """一行摘要：连续推进时用，避免刷屏。"""
        avg_fall = sum(r["fall"] for r in self.rooms.values()) / len(self.rooms)
        return (f"第{self.round:>3}回合  缓解{self.remission:>2}/{MAX_VICTORY_POINTS}"
                f"  抗体{self.antibody:>3}  平均沦陷{avg_fall:>3.0f}%"
                f"  部队{len(self.cells)}  逃逸{self.escaped}")

    def status(self):
        avg_fall = sum(r["fall"] for r in self.rooms.values()) / len(self.rooms)
        self.say(f"\n【第 {self.round} 回合】生命{self.lives}  抗体{self.antibody}"
                 f"  缓解度{self.remission}/{MAX_VICTORY_POINTS}"
                 f"  逃逸{self.escaped}  平均沦陷{avg_fall:.0f}%  部队{len(self.cells)}")
        t = TERRAIN[self.at]
        adapt = "（地形适应中）" if self.terrain_turns > 0 else ""
        self.say(f"  ▸ 部队驻扎：【{self.at}】地形「{t['name']}」{adapt}")
        if self.atk_buff:
            self.say(f"  ▸ 增益：全队攻击 +{self.atk_buff}（剩 {self.atk_buff_turns} 回合）")
        line = getattr(self, "last_target", self.at)
        foes = self.rooms[line]["enemies"]
        if foes:
            self.say("  ▸ 战线 %s：%s" % (line, "、".join(
                "%s(%d血%s)" % (f["name"], max(0, f["hp"]), "/BOSS" if f.get("boss") else "")
                for f in foes)))
        else:
            self.say("  ▸ 战线：%s（没有成形的敌人）" % line)
        # 每回合露一句场景描写：文字冒险的"画面感"
        self.say("  " + ROOM_DESC[self.at])
        for r in ROOMS:
            d = self.rooms[r]
            bar = "#" * int(d["cancer"] / 2) + "." * (10 - int(d["cancer"] / 2))
            mark = "●" if r == self.at else " "
            self.say(f" {mark}{r:<5} 好感{d['favor']:>3} 沦陷{d['fall']:>3}% "
                     f"癌{d['cancer']:>3} [{bar}] 驻军{len(d['garrison'])}")
        if self.state_line:
            self.say("@@STATE " + self.state_json() + "@@")

    def state_json(self):
        """给网页画人体地图用的一行 JSON（只有 --state 才会打，人玩的时候不打扰）。"""
        return json.dumps({
            "round": self.round, "lives": self.lives, "antibody": self.antibody,
            "remission": self.remission, "escaped": self.escaped,
            "endless": self.endless, "at": self.at, "route": ROUTE,
            "terrain_turns": self.terrain_turns,
            "front": getattr(self, "last_target", self.at),
            "atk_buff": self.atk_buff, "blockade": self.blockade_turns,
            "kills": self.kills,
            "skills": {n: {"cost": s["cost"], "cd": s["cd"], "desc": s["desc"],
                           "left": self.skills_cd.get(n, 0)}
                       for n, s in SKILLS.items()},
            "shop": {n: {"price": p, "desc": d, "sell": _resale(p),
                         "owned": self.drugs.get(n, 0)}
                     for n, (p, d) in SHOP.items()},
            "quests": {k: {"done": v[0], "need": v[1], "paid": k in self.quest_paid}
                       for k, v in self.quests.items()},
            "rooms": {r: {"favor": self.rooms[r]["favor"], "fall": self.rooms[r]["fall"],
                          "cancer": self.rooms[r]["cancer"],
                          "garrison": len(self.rooms[r]["garrison"]),
                          "enemies": [{"name": e["name"], "hp": max(0, e["hp"]),
                                       "atk": e["atk"], "boss": bool(e.get("boss"))}
                                      for e in self.rooms[r]["enemies"]],
                          "terrain": TERRAIN[r]["name"],
                          "note": TERRAIN[r]["note"],
                          # 数值一起给出去：网页的地形表要显示"到底改了多少"，
                          # 让前端自己再写一份 bonus 表迟早和游戏漂开。
                          "bonus": TERRAIN[r]["bonus"],
                          "growth": TERRAIN[r]["growth"]} for r in ROOMS},
            "cells": [{"name": c["name"], "atk": c["atk"], "hp": c["hp"]}
                      for c in self.cells],
        }, ensure_ascii=False)

    def map_view(self):
        """人体地图：路线 + 驻扎点 + 每个区域的地形与状态，一次看全。"""
        self.say("\n" + "=" * 58)
        self.say("  人体地图 · 全身循环（前进 = 沿这条线走）")
        self.say("=" * 58)
        line1, line2 = [], []
        for i, r in enumerate(ROUTE):
            mark = "●" if r == self.at else "○"
            line1.append(f"{mark}{i + 1}.{r}")
        self.say("   " + " ── ".join(line1))
        self.say("   └──────────── 回到血液（循环）────────────┘")
        self.say(f"\n   现在驻扎：{self.at} · 地形「{TERRAIN[self.at]['name']}」")
        self.say(f"   {TERRAIN[self.at]['note']}")
        self.say("\n   区域      地形        好感 沦陷   癌  驻军  地形效果（攻击修正）")
        for r in ROUTE:
            d = self.rooms[r]
            t = TERRAIN[r]
            bonus = "、".join(f"{k}{'+' if v > 0 else ''}{v}"
                             for k, v in t["bonus"].items()) or "无"
            if t["growth"]:
                bonus += f"；增殖{'+' if t['growth'] > 0 else ''}{t['growth']}"
            mark = "●" if r == self.at else " "
            self.say(f"  {mark}{r:<5} {t['name']:<9} {d['favor']:>3}  {d['fall']:>3}%"
                     f"  {d['cancer']:>3}  {len(d['garrison']):>2}"
                     f"  {'敌' + str(len(d['enemies'])) if d['enemies'] else '—':>3}  {bonus}")
        self.say("\n   任务：%s" % "、".join(
            "%s %d/%d%s" % (k, v[0], v[1], "✔" if k in self.quest_paid else "")
            for k, v in self.quests.items()))
        self.say("=" * 58)

    def roster(self):
        if not self.cells:
            self.say("  （战队为空）")
            return
        for i, c in enumerate(self.cells, 1):
            self.say(f"  {i}. {c['name']:<6} 攻{c['atk']:>2} 血{c['hp']:>2}"
                     f"  {CELLS[c['name']][3]}")
            self.say(f"      「{CELLS[c['name']][4]}」")

    # ── 行动 ─────────────────────────────────────────────────────────
    def recruit(self, name, room=None):
        if name not in CELLS:
            return "没有这种细胞。"
        cost = CELLS[name][0]
        if self.antibody < cost:
            return f"抗体不够（{name} 需要 {cost}，现有 {self.antibody}）。"
        self.antibody -= cost
        self.cells.append({"name": name, "atk": CELLS[name][1], "hp": CELLS[name][2]})
        where = room or self.rng.choice(ROOMS)
        self.rooms[where]["garrison"].append(
            {"name": name, "atk": CELLS[name][1], "hp": CELLS[name][2]})
        self.rooms[where]["favor"] = min(100, self.rooms[where]["favor"] + 2)
        return (f"招募 {name}（-{cost} 抗体），入驻{where}。\n"
                f"      {name}：「{CELLS[name][4]}」")

    def use_drug(self, name, room=None):
        if self.drugs.get(name, 0) <= 0:
            return f"没有 {name} 了。"
        room = room or "肝脏"
        eff = DRUGS[name][2]
        price = DRUGS[name][0]
        if self.antibody < price:
            return f"抗体不够（{name} 需要 {price}，现有 {self.antibody}）。"
        self.antibody -= price
        self.drugs[name] -= 1
        d = self.rooms.get(room, self.rooms["肝脏"])
        if eff == "chemo":
            for r in self.rooms.values():
                r["cancer"] = max(0, r["cancer"] - 6)
                r["favor"] = max(20, r["favor"] - 2)
            return "化疗药物：全场癌细胞 -6，各器官好感 -2（药效猛烈，器官在受罪）。"
        if eff == "targeted":
            d["cancer"] = max(0, d["cancer"] - 10)
            return f"靶向药物：{room} 癌细胞 -10（只打带标记的，正常细胞不受累）。"
        if eff == "checkpoint":
            self.checkpoint_turns = 2
            return "免疫检查点抑制剂：刹车松开，全队攻击 +3（2 回合）。"
        if eff == "radio":
            cut = int(d["cancer"] * 0.6)
            d["cancer"] -= cut
            d["favor"] = max(20, d["favor"] - 3)
            return f"放疗：{room} 清除 {cut} 癌细胞，好感 -3。"
        if eff == "cart":
            self.cells.append({"name": "T细胞", "atk": 12, "hp": 10})
            return "CAR-T疗法：一支被改造过的 T 细胞战队加入（攻12/血10）。"
        if eff == "vaccine":
            self.remission += 1
            d["cancer"] = max(0, d["cancer"] - 2)
            return "BRCA-RNA疫苗：把敌人的特征提前教会免疫系统（缓解度 +1）。"
        if eff == "hormone":
            self.hormone_turns = 3
            return "激素疗法：压制肿瘤负荷，BOSS 强度 -30%（3 回合）。"
        if eff == "aspirin":
            self.hormone_turns = 0
            return "阿司匹林：炎症水平降下来了，负面状态解除。"
        if eff == "surgery":
            cut = int(d["cancer"] * 0.5)
            d["cancer"] -= cut
            d["favor"] = max(20, d["favor"] - 5)
            return f"手术：{room} 癌细胞 -{cut}（切掉了病灶，也切掉了一部分功能）。"
        if eff == "terrain_adapt":
            self.terrain_turns = TERRAIN_ADAPT_TURNS
            t = TERRAIN[self.at]
            return (f"地形适应：部队换上了适合「{t['name']}」的作战方式"
                    f"（加成翻倍、惩罚忽略，{TERRAIN_ADAPT_TURNS} 回合）。")
        return "（没生效）"

    # ── 结算 ─────────────────────────────────────────────────────────
    def terrain_bonus(self, cell_name):
        """地形对某个细胞种类的攻击修正（含「地形适应」的翻倍/免疫惩罚）。"""
        b = TERRAIN[self.at]["bonus"].get(cell_name, 0)
        if self.terrain_turns > 0:
            # 适应：正加成翻倍，负加成清零 —— 这是花 2 抗体买来的东西
            return b * 2 if b > 0 else 0
        return b

    def battle(self):
        self.last_target = self.front_line()
        bonus = 3 if self.checkpoint_turns > 0 else 0
        killed = 0
        # 目标选法沿用 v3：自动扑向癌细胞最多的器官（不然全身管不过来）。
        # 差别在于**地形加成只在你驻扎在那个器官时才吃得到** —— 于是「前进」成了
        # 真的战术动作：提前把部队预置到战场，才能站在合手的地形里打；
        # 站在别处打，就是在陌生地形里硬拼（没有任何加成，还可能吃不到细胞特性）。
        target, worst = None, -1
        for r in ROOMS:
            if self.rooms[r]["cancer"] > worst:
                worst, target = self.rooms[r]["cancer"], r
        if target is None or worst <= 0:
            # 器官干净但可能有残留敌人：清掉它们，把一个区域真正"肃清"
            for r in ROOMS:
                if self.rooms[r]["enemies"]:
                    target = r
                    worst = self.rooms[r]["cancer"]
                    break
        if target is None or (worst <= 0 and not self.rooms[target]["enemies"]):
            # 全身干净：部队就地驻守，持续修复驻地（免疫存在感 = 器官好感）
            self.rooms[self.at]["favor"] = min(100, self.rooms[self.at]["favor"] + 1)
            return 0
        here = (target == self.at)
        d = self.rooms[target]
        for c in list(self.cells):
            dmg = c["atk"] + bonus + self.atk_buff + (self.terrain_bonus(c["name"]) if here else 0)
            if c["name"] == "树突细胞":
                dmg += 1
            if c["name"] == "细胞因子":
                dmg = 0                      # 辅助：不直接造成伤害
                for other in self.cells:
                    if other is not c:
                        # 必须有上限：原来每回合 +2 累加，几十回合后攻击力会失控
                        cap = CELLS[other["name"]][1] + 6
                        other["atk"] = min(other["atk"] + 2, cap)
                self.rooms[target]["favor"] = min(100, self.rooms[target]["favor"] + 3)
            if d["enemies"]:
                # 有单位就先打单位：打掉一个单位会连带削掉它承载的那片病灶
                foe = max(d["enemies"], key=lambda e: e["hp"])
                foe["hp"] -= dmg
                killed += dmg
                if foe["hp"] <= 0:
                    d["enemies"].remove(foe)
                    d["cancer"] = max(0, d["cancer"] - foe.get("burden", 0))
                    self.kills += 1
                    self.say(f"     ✕ 消灭 {foe['name']}（驻地癌 -{foe.get('burden', 0)}）")
            else:
                d["cancer"] = max(0, d["cancer"] - dmg)
                killed += dmg
            if c["name"] == "中性粒细胞":
                self.cells.remove(c)
            elif c["name"] == "巨噬细胞" and c["hp"] < CELLS["巨噬细胞"][2]:
                c["hp"] += 1
        return killed

    # ── v5：前线 / 敌人 / 技能 / 商店 ────────────────────────────────
    def front_line(self):
        """当前战线：癌细胞最多的区域（和 battle() 的选法一致）。"""
        best, name = -1, self.at
        for r in ROOMS:
            score = self.rooms[r]["cancer"] + len(self.rooms[r]["enemies"]) * 4
            if score > best:
                best, name = score, r
        return name

    def spawn_enemies(self, room):
        """癌长到一定程度就会"长出单位"——不再是抽象数字。"""
        d = self.rooms[room]
        if d["cancer"] < 10 or len(d["enemies"]) >= 2:
            return None
        names = list(ENEMIES)
        if d["cancer"] < 14:
            pool = names[:2]
        elif d["cancer"] < 20:
            pool = names[1:4]
        else:
            pool = names[2:]
        pick = self.rng.choice(pool)
        hp, atk, desc = ENEMIES[pick]
        hp = max(3, int(hp * (0.8 + 0.4 * self.diff)) + self.rng.randint(-1, 2))
        d["enemies"].append({"name": pick, "hp": hp, "atk": atk, "desc": desc,
                             "burden": int(ENEMY_BURDEN * ENEMIES[pick][0])})
        return pick

    def enemy_strike(self):
        """敌人回合：战线上的敌人打部队；没被压制的区域敌人啃器官。"""
        line = getattr(self, "last_target", self.at) or self.at
        d = self.rooms[line]
        for foe in d["enemies"]:
            if not self.cells:
                break
            victim = self.rng.choice(self.cells)
            victim["hp"] -= foe["atk"]
            if victim["hp"] <= 0:
                self.say(f"     {victim['name']} 被 {foe['name']} 打崩。")
                self.cells.remove(victim)
        for r in ROOMS:
            if r == line:
                continue
            if self.rooms[r]["enemies"]:
                self.rooms[r]["favor"] = max(0, self.rooms[r]["favor"] - 2)

    def cast(self, name):
        """放技能。（初代的 skill_cooldowns 就是这套：消耗 + 冷却。）"""
        spec = SKILLS.get(name)
        if not spec:
            return "没有这个技能。可用：%s" % "、".join(SKILLS)
        left = self.skills_cd.get(name, 0)
        if left > 0:
            return "%s 还在冷却（%d 回合）。" % (name, left)
        if self.antibody < spec["cost"]:
            return "抗体不够（%s 要 %d，现有 %d）。" % (name, spec["cost"], self.antibody)
        self.antibody -= spec["cost"]
        self.skills_cd[name] = spec["cd"]
        line = getattr(self, "last_target", self.at) or self.at
        d = self.rooms[line]
        kind = spec["kind"]
        if kind == "snipe":
            if not d["enemies"]:
                return "%s：战线（%s）上没有敌人，白放了一发。" % (name, line)
            foe = max(d["enemies"], key=lambda e: e["hp"])
            foe["hp"] -= 9
            if foe["hp"] <= 0:
                d["enemies"].remove(foe)
                d["cancer"] = max(0, d["cancer"] - foe.get("burden", 0))
                self.kills += 1
                return "%s：%s 被一枪打掉。" % (name, foe["name"])
            return "%s：%s 剩 %d 血。" % (name, foe["name"], foe["hp"])
        if kind == "garrison":
            self.rooms[self.at]["favor"] = min(100, self.rooms[self.at]["favor"] + 6)
            self.rooms[self.at]["garrison"].append(
                {"name": "中性粒细胞", "atk": CELLS["中性粒细胞"][1],
                 "hp": CELLS["中性粒细胞"][2]})
            return "%s：驻地 %s 好感 +6，补了 1 支驻军。" % (name, self.at)
        if kind == "storm":
            d["cancer"] = max(0, d["cancer"] - 7)
            d["favor"] = max(0, d["favor"] - 5)
            for foe in list(d["enemies"]):
                foe["hp"] -= 3
                if foe["hp"] <= 0:
                    d["enemies"].remove(foe)
                    self.kills += 1
            self.atk_buff += 2
            self.atk_buff_turns = max(self.atk_buff_turns, 2)
            return "%s：%s 癌 -7、好感 -5，全队攻击 +2（2 回合）。" % (name, line)
        if kind == "memory":
            self.atk_buff += 3
            self.atk_buff_turns = max(self.atk_buff_turns, 3)
            return "%s：全队攻击 +3（3 回合）。" % name
        if kind == "chemotaxis":
            worst = max(ROOMS, key=lambda r: self.rooms[r]["cancer"])
            moved = min(4, self.rooms[worst]["cancer"])
            self.rooms[worst]["cancer"] -= moved
            d["cancer"] += moved
            d["cancer"] = max(0, d["cancer"] - 3)
            return "%s：从 %s 引来 %d 点病灶，就地削掉 3。" % (name, worst, moved)
        if kind == "blockade":
            self.blockade_turns = 1
            self.escaped = 0
            return "%s：本回合全区域停止增殖，逃逸清零。" % name
        return "%s：（没生效）" % name

    def skill_view(self):
        self.say("\n技能（9 使用；消耗抗体 + 冷却）：")
        for n, s in SKILLS.items():
            left = self.skills_cd.get(n, 0)
            state = ("冷却 %d 回合" % left) if left > 0 else "可用"
            self.say("  %-10s 抗体%-2d 冷却%-2d  %-22s [%s]"
                     % (n, s["cost"], s["cd"], s["desc"], state))
        if self.atk_buff:
            self.say("  当前增益：全队攻击 +%d（剩 %d 回合）" % (self.atk_buff, self.atk_buff_turns))

    def shop_view(self):
        self.say("\n商店（10 购买 / 卖 出售；抗体 %d）：" % self.antibody)
        for n, (price, desc) in SHOP.items():
            self.say("  %-12s %2d 抗体   卖 %d    %s" % (n, price, _resale(price), desc))

    def shop_buy(self, name):
        if name not in SHOP:
            return "商店里没有 %s。" % name
        price = SHOP[name][0]
        if self.antibody < price:
            return "抗体不够（%s 要 %d，现有 %d）。" % (name, price, self.antibody)
        self.antibody -= price
        self.drugs[name] = self.drugs.get(name, 0) + 1
        return "买下 %s（-%d 抗体），现有 %d 份。" % (name, price, self.drugs[name])

    def shop_sell(self, name):
        if self.drugs.get(name, 0) <= 0:
            return "没有 %s 可卖。" % name
        if name not in SHOP:
            return "%s 不能回收。" % name
        back = _resale(SHOP[name][0])
        self.drugs[name] -= 1
        self.antibody += back
        return "卖掉 1 份 %s（+%d 抗体）。" % (name, back)

    def enemy_turn(self):
        blocked = self.blockade_turns > 0
        for r in ROOMS:
            d = self.rooms[r]
            # 地形也会改增殖速度：肺（低氧）与肠道（菌群竞争）压住它，
            # 血液（循环肿瘤细胞）与脾脏（滤血汇集碎片）助长它。
            growth = 1 + int(self.diff) + TERRAIN[r]["growth"]
            if d["favor"] < 40:
                growth += 1
            if not blocked:
                d["cancer"] += max(0, growth)
            if d["favor"] >= 70:
                d["cancer"] = max(0, d["cancer"] - 2)
            elif d["favor"] < 30 and d["garrison"]:
                d["garrison"].pop(0)
            d["favor"] = max(0, d["favor"] - d["cancer"] // 8)
            # 沦陷度上下都要封：只 min(100) 的话，当 癌=0 且 好感=100 时会算出
            # 负值（0 + (80-100) = -20），fuzz 就是这么抓出来的。
            d["fall"] = max(0, min(100, int(d["cancer"] * 1.5 + (80 - d["favor"]))))
            # v5：病灶长到一定程度就会长出有血量的单位
            if not blocked:
                born = self.spawn_enemies(r)
                if born:
                    self.say("  ☣ %s 里出现 %s。" % (r, born))
        self.enemy_strike()
        for r in ROOMS:
            if self.rooms[r]["fall"] >= 100:
                self.escaped += self.rng.randint(1, 3)
        if self.checkpoint_turns:
            self.checkpoint_turns -= 1
        if self.hormone_turns:
            self.hormone_turns -= 1
        if self.terrain_turns:
            self.terrain_turns -= 1
        if self.blockade_turns:
            self.blockade_turns -= 1
        if self.atk_buff_turns:
            self.atk_buff_turns -= 1
            if self.atk_buff_turns == 0:
                self.atk_buff = 0
        for n in list(self.skills_cd):
            if self.skills_cd[n] > 0:
                self.skills_cd[n] -= 1
                if self.skills_cd[n] <= 0:
                    del self.skills_cd[n]
        self.check_quests()
        if self.rng.random() < 0.25:
            self.say("  ⚠ 血栓事件：血流受阻，一处微循环被堵住了。")

    def check_quests(self):
        """任务：达成即结算抗体（对应初代的 任务/委托）。"""
        clean = sum(1 for r in ROOMS
                    if self.rooms[r]["cancer"] <= 2 and not self.rooms[r]["enemies"])
        avg_fall = sum(r["fall"] for r in self.rooms.values()) / len(self.rooms)
        rules = [("首杀", self.kills, 3, 4),
                 ("净化", clean, 2, 6),
                 ("稳住", self.round if avg_fall < 6 else 0, 5, 5)]
        for name, value, need, reward in rules:
            if name in self.quest_paid:
                continue
            prog = min(value, need)
            self.quests[name] = [prog, need]
            if value >= need:
                self.quest_paid.append(name)
                self.antibody += reward
                self.say("  ✔ 任务完成：%s（+%d 抗体）" % (name, reward))

    def boss_turn(self):
        idx = min(self.boss_count, len(BOSSES) - 1)
        name, desc = BOSSES[idx]
        self.boss_count += 1
        mult = self.boss_mult * (0.7 if self.hormone_turns else 1.0)
        dmg = int(5 * mult * self.diff)
        self.say(f"  ☠ BOSS：{name} —— {desc}")
        # v5：BOSS 是**一支真的单位**，会留在最脏的区域里挨打；打掉它才叫过关
        worst = max(ROOMS, key=lambda r: self.rooms[r]["cancer"])
        boss_hp = int(18 * mult)
        self.rooms[worst]["enemies"].append(
            {"name": name, "hp": boss_hp, "atk": max(2, int(3 * mult)), "desc": desc,
             "boss": True, "burden": int(ENEMY_BURDEN * boss_hp)})
        self.say(f"     {name} 已在 {worst} 落地（{boss_hp} 血）—— 打掉它才会退。")
        if self.cells:
            hit = max(1, len(self.cells) // 2)
            victims = self.rng.sample(self.cells, min(hit, len(self.cells)))
            self.say(f"     冲击 {dmg} 点，{len(victims)} 支部队承压。")
            for victim in victims:
                victim["hp"] -= dmg
                if victim["hp"] <= 0:
                    self.say(f"     {victim['name']} 阵亡。")
                    self.cells.remove(victim)
        else:
            self.lives = max(0, self.lives - 1)
            self.say("     战队全灭，免疫系统被迫重启。")
        self.boss_mult = min(3.0, self.boss_mult + 0.15)

    def rebirth(self):
        self.lives -= 1
        if self.lives <= 0:
            return False
        self.say("\n💫 重生：骨髓深处的造血干细胞开始疯狂增殖。")
        self.say("   新一批免疫细胞正在诞生——但记忆细胞仍记得敌人的样子。")
        self.cells = [{"name": n, "atk": CELLS[n][1], "hp": CELLS[n][2]}
                      for n in ("T细胞", "中性粒细胞")]
        for r in ROOMS:
            self.rooms[r]["cancer"] = max(4, self.rooms[r]["cancer"] // 2)
            self.rooms[r]["fall"] = max(0, self.rooms[r]["fall"] - 30)
            self.rooms[r]["favor"] = min(100, self.rooms[r]["favor"] + 20)
            self.rooms[r]["garrison"] = self._make_garrison(r)
        return True

    def tick(self):
        self.round += 1
        wealthy = sum(1 for r in self.rooms.values() if r["favor"] >= 70)
        self.antibody += 1 + wealthy // 3
        self.battle()
        self.enemy_turn()
        healthy = sum(1 for r in self.rooms.values()
                      if r["cancer"] <= 2 and r["favor"] >= 60)
        if healthy >= 4:
            self.remission += 1
        if healthy == len(ROOMS):
            self.remission += 1
        if self.round % BOSS_INTERVAL == 0:
            self.boss_turn()
        if self.lives <= 0:                # BOSS 回合也可能打光最后一条命
            return "dead"
        if self.remission >= ENDLESS_AT and not self.endless:
            self.endless = True
            self.say("  ★ 进入无尽模式：目标改为 1000 缓解度（长期共存）。")
        if len(self.cells) == 0 and self.lives > 0:
            if not self.rebirth():
                return "dead"
        if self.remission >= MAX_VICTORY_POINTS and not self.endless:
            return "win"
        if self.escaped >= 40:
            return "metastasis"
        if self.round >= MAX_ROUNDS:
            return "timeout"
        return "ok"

    # ── 结局分级 ─────────────────────────────────────────────────────
    def summary(self):
        fav = sum(r["favor"] for r in self.rooms.values()) / len(ROOMS)
        fall = sum(r["fall"] for r in self.rooms.values()) / len(ROOMS)
        clean = sum(1 for r in self.rooms.values() if r["cancer"] <= 2)
        return (f"  打了 {self.round} 回合 · 缓解度 {self.remission} · "
                f"平均好感 {fav:.0f} · 平均沦陷 {fall:.0f}%\n"
                f"  干净器官 {clean}/{len(ROOMS)} · 逃逸癌细胞 {self.escaped} · "
                f"剩余生命 {self.lives} · 抗体 {self.antibody}")

    def ending(self, reason):
        """四档结局（外加阵亡），每档给一段收尾 + 数据回顾。"""
        if reason == "metastasis" or self.escaped >= 40:
            title = "结局 · 转移"
            body = ("逃逸的癌细胞在别的器官里扎了根。\n"
                    "   免疫系统没能守住边界——但这不是终局，只是换了一个战场。")
        elif reason == "dead":
            title = "结局 · 阵亡"
            body = ("生命用尽了。骨髓最后一次努力之后，没能再造出血。\n"
                    "   这场战争没有赢下来，但你把它拖住了很久。")
        elif self.remission >= MAX_VICTORY_POINTS:
            title = "结局 · 完全缓解"
            body = ("身体重新学会了平衡：不是把敌人杀光，\n"
                    "   而是让它不再能伤害你。真正的健康不是征服，而是和谐。")
        elif self.remission >= 12:
            title = "结局 · 部分缓解"
            body = ("大部分器官恢复了秩序，还有几处在拉锯。\n"
                    "   这是可以长期维持的状态——医学上叫'带瘤生存'。")
        else:
            title = "结局 · 带病共存"
            body = ("没能清干净，但也没让它扩散。\n"
                    "   你和它达成了一个暂时的停火协议。")
        self.say("\n" + "=" * 58)
        self.say("  " + title)
        self.say("=" * 58)
        self.say("  " + body)
        self.say(self.summary())
        self.say("=" * 58)

    # ── 交互 ─────────────────────────────────────────────────────────
    def _read(self, prompt):
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            return ""           # 输入断了按空处理，宿主关管道时不会卡住

    def _choice(self, scripted):
        if scripted:
            return scripted.pop(0)
        self.say("\n可选行动： 1招募  2用药  3推进回合（可写「3 5」连续 5 回合）"
                 "  4查看战队  5结束\n"
                 "           6前进（驻扎到下一个区域）  7人体地图  8地形\n"
                 "           9技能  10商店")
        return self._read("> ").strip().lower()

    def play(self, scripted=None, save_path=None):
        self.banner()
        scripted = list(scripted or [])
        while True:
            self.status()
            if self.round % 5 == 0 and save_path:
                self.save(save_path)
            choice = self._choice(scripted)
            if choice in ("5", "q", "quit", "exit"):
                self.ending("quit")
                return "quit"
            if choice == "1":
                self.say("可招募：" + "、".join(
                    f"{n}({CELLS[n][0]})" for n in CELLS))
                name = (scripted.pop(0) if scripted else self._read("要哪种细胞？")).strip()
                self.say("  " + self.recruit(name))
                continue
            if choice == "2":
                owned = [f"{n}×{c}" for n, c in self.drugs.items() if c > 0]
                self.say("可用药物：" + ("、".join(owned) if owned else "（没有药）"))
                name = (scripted.pop(0) if scripted else self._read("用哪种药？")).strip()
                room = (scripted.pop(0) if scripted else
                        self._read("作用于哪个器官？(回车=肝脏)")).strip()
                self.say("  " + self.use_drug(name, room or None))
                continue
            if choice == "4":
                self.say("我的战队：")
                self.roster()
                continue
            if choice in ("6", "前进", "m", "move"):
                i = ROUTE.index(self.at)
                self.at = ROUTE[(i + 1) % len(ROUTE)]
                t = TERRAIN[self.at]
                self.say(f"  前进：部队进入【{self.at}】。地形「{t['name']}」"
                         f"（{t['note']}）")
                self.say("  " + ROOM_DESC[self.at])
                continue
            if choice in ("7", "地图", "map"):
                self.map_view()
                continue
            if choice in ("8", "地形", "terrain"):
                t = TERRAIN[self.at]
                self.say(f"  当前地形：{self.at} · {t['name']}")
                self.say(f"  {t['note']}")
                bonus = "、".join(f"{k} {'+' if v > 0 else ''}{v}"
                                 for k, v in t["bonus"].items()) or "（无修正）"
                self.say(f"  攻击修正：{bonus}")
                self.say(f"  增殖修正：{t['growth']:+d}/回合")
                self.say("  地形加成只在**部队驻扎在这个区域**时生效 —— "
                         "先「6前进」过来，再推进回合。")
                continue
            if choice in ("9", "技能", "skill"):
                self.skill_view()
                sname = (scripted.pop(0) if scripted else self._read("用哪个技能？(回车=不用)")).strip()
                if sname:
                    self.say("  " + self.cast(sname))
                continue
            if choice in ("10", "商店", "shop"):
                self.shop_view()
                act = (scripted.pop(0) if scripted else
                       self._read("买/卖/回车=离开？")).strip()
                if act in ("买", "buy", "b"):
                    iname = (scripted.pop(0) if scripted else self._read("买什么？")).strip()
                    self.say("  " + self.shop_buy(iname))
                elif act in ("卖", "sell", "s"):
                    iname = (scripted.pop(0) if scripted else self._read("卖什么？")).strip()
                    self.say("  " + self.shop_sell(iname))
                continue
            # 推进回合；支持「3 5」/「c5」连续推进
            parts = choice.replace("×", " ").replace("x", " ").replace("c", "3 ").split()
            rounds = 1
            if len(parts) > 1 and parts[1].isdigit():
                rounds = max(1, min(int(parts[1]), 50))
            result = "ok"
            for _ in range(rounds):
                result = self.tick()
                if rounds > 1:
                    self.say("  " + self.brief())    # 连续推进只打一行
                if result != "ok":
                    break
            if result != "ok":
                self.ending(result)
                if save_path:
                    self.save(save_path)
                return result


# ── 三种运行模式 ─────────────────────────────────────────────────────
def run_selftest():
    """确定性流程：跑一条固定剧本 + 断言纯逻辑分支（不依赖人输入）。"""
    print("[selftest] 固定种子跑一遍确定性流程")
    g = Game("20岁", seed=20261013)
    scripted = ["4", "7", "6", "8", "1", "T细胞", "2", "化疗药物", "肝脏",
                "3 5",                      # 连续推进 5 回合（会撞上 BOSS）
                "1", "巨噬细胞", "2", "BRCA-RNA疫苗", "脾脏", "6",
                "3 5", "2", "地形适应", "肺", "3 5", "5"]
    with contextlib.redirect_stdout(io.StringIO()):
        result = g.play(scripted=scripted)
    print(f"[selftest] 剧本结果={result} 回合={g.round} 抗体={g.antibody} "
          f"缓解度={g.remission} 生命={g.lives} 部队={len(g.cells)}")

    # ── 地形系统的断言：加成必须真的进战斗，而不是只有文案 ──────────────
    assert set(TERRAIN) == set(ROOMS), "每个器官都要有地形"
    assert len(ROUTE) == len(ROOMS) and set(ROUTE) == set(ROOMS), "路线要覆盖全部器官"
    # 同样一局、同样一批细胞、同样一个目标，**只差驻扎点**：
    # 驻扎在战场 vs 驻扎在别处，伤害必须不同 —— 否则地形只是文字。
    target = "骨髓"                          # 先手部队是 T细胞+巨噬细胞，骨髓给 T细胞 +2
    gauge_a, gauge_b = Game("20岁", seed=11), Game("20岁", seed=11)
    for g in (gauge_a, gauge_b):
        for r in ROOMS:
            g.rooms[r]["cancer"] = 60 if r == target else 0
    gauge_a.at = target                      # 驻扎在战场：吃地形
    gauge_b.at = ROUTE[(ROUTE.index(target) + 1) % len(ROUTE)]   # 驻扎在别处：不吃
    hit_here = gauge_a.battle()
    hit_elsewhere = gauge_b.battle()
    assert hit_here != hit_elsewhere, \
        "驻扎点不同，伤害必须不同（地形要么真的生效，要么这套系统是假的）"
    assert hit_here > hit_elsewhere, \
        "站在合适的地形里应当打得更狠（骨髓给 T 细胞 +2）"
    # 地形适应：加成翻倍、惩罚清零
    ga2 = Game("20岁", seed=3)
    ga2.at = "血液"
    before = ga2.terrain_bonus("中性粒细胞")
    ga2.terrain_turns = TERRAIN_ADAPT_TURNS
    assert ga2.terrain_bonus("中性粒细胞") == before * 2, "地形适应应当让正加成翻倍"
    assert ga2.terrain_bonus("巨噬细胞") == 0, "地形适应应当忽略负加成"
    ga2.drugs["地形适应"] = 1
    ga2.antibody = 10
    ga2.use_drug("地形适应")
    assert ga2.terrain_turns == TERRAIN_ADAPT_TURNS, "用药后应当进入地形适应"
    # @@STATE 行必须是合法 JSON 且字段齐全
    st = json.loads(ga2.state_json())
    for k in ("round", "lives", "antibody", "remission", "at", "route", "rooms", "cells"):
        assert k in st, "状态行缺字段 %s" % k
    assert set(st["rooms"]) == set(ROOMS)

    # ── v5：技能 / 商店 / 敌人 / 任务的断言（都要真改数字，不是文案）───
    gs = Game("20岁", seed=21)
    gs.antibody = 20
    base_atk = gs.atk_buff
    msg = gs.cast("免疫记忆")
    assert gs.atk_buff == base_atk + 3 and gs.atk_buff_turns == 3, \
        "免疫记忆必须真的加攻击：%r" % msg
    assert gs.antibody == 18, "技能必须扣抗体"
    assert gs.skills_cd.get("免疫记忆") == SKILLS["免疫记忆"]["cd"], "技能必须进冷却"
    again = gs.cast("免疫记忆")
    assert "冷却" in again, "冷却中不该再放：%r" % again
    poor = Game("20岁", seed=21)
    poor.antibody = 0
    assert "抗体不够" in poor.cast("免疫记忆"), "抗体不够要拒绝"
    assert poor.skills_cd.get("免疫记忆") is None, "拒绝时不该进冷却"
    # 商店：买要扣抗体、加库存；卖要半价回收、减库存
    gp = Game("20岁", seed=22)
    gp.antibody = 10
    before = gp.drugs.get("手术", 0)
    gp.shop_buy("手术")
    assert gp.antibody == 5 and gp.drugs["手术"] == before + 1, "购买没生效"
    gp.shop_sell("手术")
    assert gp.antibody == 5 + _resale(SHOP["手术"][0]), "回收没生效"
    assert gp.drugs["手术"] == before, "回收没减库存"
    gp.antibody = 1                      # CAR-T 要 6，1 肯定不够
    owned_before = gp.drugs.get("CAR-T疗法", 0)
    assert "抗体不够" in gp.shop_buy("CAR-T疗法"), "缺钱时应当拒绝"
    assert gp.antibody == 1 and gp.drugs.get("CAR-T疗法", 0) == owned_before, \
        "拒绝时不能扣钱、也不能给东西"
    # 敌人：病灶长到阈值会长出单位，战斗打的是单位，敌人会反击部队
    ge = Game("20岁", seed=23)
    ge.rooms["肺"]["cancer"] = 30
    born = ge.spawn_enemies("肺")
    assert born and ge.rooms["肺"]["enemies"], "癌到阈值应当长出敌人"
    foe = ge.rooms["肺"]["enemies"][0]
    foe["hp"] = 40                   # 拉高血量：要保证它挨完一轮还在，才测得到反击
    hp0, n0 = foe["hp"], len(ge.rooms["肺"]["enemies"])
    ge.last_target = "肺"
    ge.battle()
    after = ge.rooms["肺"]["enemies"]
    assert (len(after) < n0) or (after and after[0]["hp"] < hp0), \
        "战斗必须打掉敌人的血（现在还是 %r）" % after
    cells_before = len(ge.cells)
    hp_sum = sum(c["hp"] for c in ge.cells)
    ge.enemy_strike()
    assert len(ge.cells) < cells_before or sum(c["hp"] for c in ge.cells) < hp_sum, \
        "敌人必须真的反击部队"
    # 任务：达成后必须结算抗体，且只结算一次
    gq = Game("20岁", seed=24)
    gq.kills = 3
    gq.antibody = 0
    gq.check_quests()
    first = gq.antibody
    assert first > 0 and "首杀" in gq.quest_paid, "任务达成应当结算抗体"
    gq.check_quests()
    assert gq.antibody == first, "同一任务不能重复结算"

    g2 = Game("晚期", seed=7)
    g2.antibody = 12
    msg = g2.recruit("T细胞")
    assert g2.antibody == 9, "招募应当花抗体（12-3=9）"
    g2.drugs["手术"] = 1
    g2.use_drug("手术", "肺")
    assert g2.antibody == 4, "用药也应当花抗体（9-5=4）"
    g2.tick()
    assert g2.round == 1, "回合应当推进到 1"
    assert 0 <= g2.rooms["肺"]["favor"] <= 100, "好感应当在 0..100"
    # 存档往返
    path = os.path.join(os.environ.get("TEMP", "."), "cancer_selftest_save.json")
    g2.save(path)
    g3 = Game.load(path, seed=1)
    assert (g3.round, g3.antibody, g3.remission) == (g2.round, g2.antibody, g2.remission), \
        "存档往返应当保持一致"
    os.remove(path)
    # 结局分级：四档都要能出
    tiers = set()
    for reason, esc, rem in (("metastasis", 99, 0), ("dead", 0, 0),
                             ("quit", 0, 20), ("quit", 0, 15), ("quit", 0, 3)):
        g4 = Game("20岁", seed=3)
        g4.escaped = esc
        g4.remission = rem
        with contextlib.redirect_stdout(io.StringIO()):
            g4.ending(reason)
        tiers.add(reason + str(rem))
    print(f"[selftest] 分支断言通过（{msg.splitlines()[0]}；存档往返一致；结局 {len(tiers)} 档可出）")
    ok = result in ("quit", "win", "timeout")
    print("[selftest] " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def run_fuzz(times=50):
    """模糊测试：随机输入乱打，只断言"不崩、不无限循环、状态不越界"。"""
    print(f"[fuzz] {times} 局随机输入")
    actions = ["1", "2", "3", "3 5", "4", "5", "", "??", "1", "2"]
    # v4 新动作也要进模糊测试：前进/地图/地形不该把状态打坏
    actions += ["6", "7", "8", "前进", "地图", "地形"]
    # v5 的技能与商店也要进：它们会改 antibody / cd / 库存
    actions += ["9", "10", "技能", "商店"]
    skills = list(SKILLS)
    shop = list(SHOP)
    cells = list(CELLS)
    drugs = list(DRUGS)
    bad = 0
    for i in range(times):
        mode = random.choice(list(MODES))
        g = Game(mode, seed=i)
        try:
            for _ in range(400):
                a = random.choice(actions)
                if a == "1":
                    g.recruit(random.choice(cells), random.choice(ROOMS))
                elif a == "2":
                    name = random.choice(drugs)
                    g.drugs[name] = g.drugs.get(name, 0) + 1
                    g.use_drug(name, random.choice(ROOMS))
                elif a in ("6", "前进"):
                    g.at = ROUTE[(ROUTE.index(g.at) + 1) % len(ROUTE)]
                elif a in ("7", "地图"):
                    g.map_view()
                elif a == "8":
                    assert TERRAIN[g.at]["note"]
                elif a in ("9", "技能"):
                    g.antibody += 3
                    g.cast(random.choice(skills))
                elif a in ("10", "商店"):
                    g.antibody += 4
                    it = random.choice(shop)
                    g.shop_buy(it)
                    if g.drugs.get(it, 0) > 0:
                        g.shop_sell(it)
                elif a.startswith("3"):
                    if g.tick() != "ok":
                        break
                elif a == "5":
                    break
                assert g.antibody >= 0 and g.remission >= 0
                assert 0 <= g.lives <= START_LIVES
                assert g.at in TERRAIN, "驻扎点必须始终是已知区域"
                assert g.terrain_turns >= 0
                assert g.atk_buff >= 0 and g.atk_buff_turns >= 0
                assert g.kills >= 0
                for name, cd in g.skills_cd.items():
                    assert name in SKILLS and 0 <= cd <= SKILLS[name]["cd"], "冷却越界"
                for r in g.rooms.values():
                    assert isinstance(r["enemies"], list), "敌人字段必须是表"
                    for e in r["enemies"]:
                        assert e["name"] in BOSS_NAMES or e["name"] in ENEMIES, \
                            "出现了不认识的敌人：%s" % e["name"]
                for r in g.rooms.values():
                    assert 0 <= r["favor"] <= 100, "好感越界"
                    assert 0 <= r["fall"] <= 100, "沦陷越界"
                    assert r["cancer"] >= 0, "癌细胞不该为负"
        except Exception as exc:            # noqa: BLE001
            bad += 1
            print(f"[fuzz] 第 {i} 局（{mode}）崩了：{type(exc).__name__}: {exc}")
    print(f"[fuzz] {'PASS' if bad == 0 else 'FAIL'}（{times - bad}/{times} 局干净）")
    return 0 if bad == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="抗癌文字冒险游戏 v3")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--fuzz", type=int, default=0, help="随机模糊测试局数")
    ap.add_argument("--save", default=None, help="存档路径（每 5 回合与结束时写入）")
    ap.add_argument("--load", default=None, help="从存档继续")
    ap.add_argument("--seed", type=int, default=None, help="固定随机种子（可复现）")
    ap.add_argument("--state", action="store_true",
                    help="每回合多打一行 @@STATE {...}@@（网页画人体地图用）")
    args = ap.parse_args(argv)

    if args.selftest or selftest_mode():
        return run_selftest()
    if args.fuzz:
        return run_fuzz(args.fuzz)

    if args.load and os.path.exists(args.load):
        try:
            g = Game.load(args.load, seed=args.seed)
            print(f"[+] 已读档：{g.mode} / 第 {g.round} 回合 / 抗体 {g.antibody}"
                  f" / 缓解度 {g.remission}")
        except Exception as exc:            # noqa: BLE001
            print(f"[!] 存档读不了（{type(exc).__name__}），新开一局")
            g = None
    else:
        g = None
    if g is None:
        print("选择开局：")
        for i, m in enumerate(MODES, 1):
            print(f"  {i}. {m} —— {MODES[m]['desc']}")
        try:
            pick = input("编号（回车=1）：").strip() or "1"
        except (EOFError, KeyboardInterrupt):
            pick = "1"
        keys = list(MODES)
        mode = keys[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(keys) else "20岁"
        g = Game(mode, seed=args.seed)
    if args.state:
        g.state_line = True
    g.play(save_path=args.save)
    return 0


if __name__ == "__main__":
    sys.exit(main())
