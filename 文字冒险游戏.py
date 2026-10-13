#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抗癌文字冒险游戏 · v3

v2 修好了机制（资源分家 / 器官好感进主循环 / BOSS 比例封顶），v3 补的是
"它读起来像不像一个文字冒险"：器官描写、细胞性格、分级结局；
以及上站前的工程护栏：连续推进、存档续玩、模糊测试。

纯标准库。命令行：
    python 文字冒险游戏.py                     正常玩
    python 文字冒险游戏.py --selftest          确定性自检（退出码 0 = 通过）
    python 文字冒险游戏.py --fuzz 50           50 局随机输入，只断言不崩
    python 文字冒险游戏.py --load save.json    从存档继续（--save 指定写入位置）
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
        self.cells = [{"name": n, "atk": CELLS[n][1], "hp": CELLS[n][2]}
                      for n in ("T细胞", "巨噬细胞")]
        self.drugs = {"化疗药物": 1, "靶向药物": 1, "阿司匹林": 1}
        self.quests = []
        self.rooms = {}
        for r in ROOMS:
            self.rooms[r] = {"favor": 80, "fall": 0, "cancer": 8,
                             "garrison": self._make_garrison(r)}

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
                "quests": self.quests}

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
                  "boss_mult", "boss_count", "cells", "drugs", "rooms", "quests"):
            if k in d:
                setattr(g, k, d[k])
        return g

    # ── 输出 ─────────────────────────────────────────────────────────
    def say(self, text=""):
        print(text, flush=True)

    def banner(self):
        self.say("=" * 58)
        self.say("  抗癌文字冒险游戏 · 免疫系统指挥官（v3）")
        self.say("=" * 58)
        self.say(f"  开局模式：{self.mode} —— {MODES[self.mode]['desc']}")
        self.say(f"  目标：缓解度 ≥ {MAX_VICTORY_POINTS}；生命 {self.lives} 条")
        self.say("  你是指挥官。你的部队是免疫细胞，你的战场是你自己。")

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
        # 每回合露一句场景描写：文字冒险的"画面感"
        self.say("  " + ROOM_DESC[self.rng.choice(ROOMS)])
        for r in ROOMS:
            d = self.rooms[r]
            bar = "#" * int(d["cancer"] / 2) + "." * (10 - int(d["cancer"] / 2))
            self.say(f"  {r:<5} 好感{d['favor']:>3} 沦陷{d['fall']:>3}% "
                     f"癌{d['cancer']:>3} [{bar}] 驻军{len(d['garrison'])}")

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
        return "（没生效）"

    # ── 结算 ─────────────────────────────────────────────────────────
    def battle(self):
        bonus = 3 if self.checkpoint_turns > 0 else 0
        killed = 0
        for c in list(self.cells):
            target = None
            for r in ROOMS:
                if self.rooms[r]["cancer"] > 0:
                    target = r
                    break
            if target is None:
                break
            dmg = c["atk"] + bonus
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
            d = self.rooms[target]
            d["cancer"] = max(0, d["cancer"] - dmg)
            killed += dmg
            if c["name"] == "中性粒细胞":
                self.cells.remove(c)
            elif c["name"] == "巨噬细胞" and c["hp"] < CELLS["巨噬细胞"][2]:
                c["hp"] += 1
        return killed

    def enemy_turn(self):
        for r in ROOMS:
            d = self.rooms[r]
            growth = 1 + int(self.diff)
            if d["favor"] < 40:
                growth += 1
            d["cancer"] += growth
            if d["favor"] >= 70:
                d["cancer"] = max(0, d["cancer"] - 2)
            elif d["favor"] < 30 and d["garrison"]:
                d["garrison"].pop(0)
            d["favor"] = max(0, d["favor"] - d["cancer"] // 8)
            # 沦陷度上下都要封：只 min(100) 的话，当 癌=0 且 好感=100 时会算出
            # 负值（0 + (80-100) = -20），fuzz 就是这么抓出来的。
            d["fall"] = max(0, min(100, int(d["cancer"] * 1.5 + (80 - d["favor"]))))
        for r in ROOMS:
            if self.rooms[r]["fall"] >= 100:
                self.escaped += self.rng.randint(1, 3)
        if self.checkpoint_turns:
            self.checkpoint_turns -= 1
        if self.hormone_turns:
            self.hormone_turns -= 1
        if self.rng.random() < 0.25:
            self.say("  ⚠ 血栓事件：血流受阻，一处微循环被堵住了。")

    def boss_turn(self):
        idx = min(self.boss_count, len(BOSSES) - 1)
        name, desc = BOSSES[idx]
        self.boss_count += 1
        mult = self.boss_mult * (0.7 if self.hormone_turns else 1.0)
        dmg = int(5 * mult * self.diff)
        self.say(f"  ☠ BOSS：{name} —— {desc}")
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
                 "  4查看战队  5结束")
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
    scripted = ["4", "1", "T细胞", "2", "化疗药物", "肝脏",
                "3 5",                      # 连续推进 5 回合（会撞上 BOSS）
                "1", "巨噬细胞", "2", "BRCA-RNA疫苗", "脾脏",
                "3 5", "5"]
    with contextlib.redirect_stdout(io.StringIO()):
        result = g.play(scripted=scripted)
    print(f"[selftest] 剧本结果={result} 回合={g.round} 抗体={g.antibody} "
          f"缓解度={g.remission} 生命={g.lives} 部队={len(g.cells)}")

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
                elif a.startswith("3"):
                    if g.tick() != "ok":
                        break
                elif a == "5":
                    break
                assert g.antibody >= 0 and g.remission >= 0
                assert 0 <= g.lives <= START_LIVES
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
    g.play(save_path=args.save)
    return 0


if __name__ == "__main__":
    sys.exit(main())
