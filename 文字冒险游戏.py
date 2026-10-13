#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抗癌文字冒险游戏 · 重写版（v2）

为什么重写：仓库里那份 496KB 的 `文字冒险游戏.py` 有约 1155 行字符串被破坏
（本该收尾的 `\\n")` 被替换成「列表」「胃」之类的词），而 git 里只有一个提交，
没有干净版本可回滚 —— 机械修复不可能，所以按原设计重写。

保留的原设计：
  · 五种开局：20岁 / 30岁 / 40岁 / 80岁 / 晚期（难度递增）
  · 招牌机制「器官驻军」：每个器官有 好感度(favor) 与 沦陷度(fall)，
    驻军细胞按器官偏好生成（减少逐个 random 分支）
  · 免疫细胞战队：T细胞 / NK / 巨噬 / 树突 / 中性粒 / B细胞 / 细胞因子
  · 药物与治疗：化疗、靶向、免疫检查点、放疗、CAR-T、疫苗……
  · 每 5 回合一个 BOSS，强度倍数递增；胜利点累计到 max_victory_points 通关
  · 3 条命（重生）；逃逸癌细胞、血栓事件、援军、委托任务

宿主兼容：纯标准库；`SELFTEST=1`（或 `--selftest`）跑一遍确定性流程后退出，
不依赖人输入，方便网页宿主做冒烟测试。
"""

import os
import random
import sys


# ── 常量（沿用原版的数值）────────────────────────────────────────────
MAX_VICTORY_POINTS = 20
START_LIVES = 3
BOSS_INTERVAL = 5
MAX_ROUNDS = 200
ENDLESS_MILESTONES = (500, 1000)

MODES = {
    "20岁":  {"diff": 1.0, "desc": "年轻、免疫系统强健，容错最高"},
    "30岁":  {"diff": 1.3, "desc": "开始有生活压力，恢复变慢"},
    "40岁":  {"diff": 1.6, "desc": "器官功能下行，癌细胞更顽固"},
    "80岁":  {"diff": 2.0, "desc": "高龄，免疫衰老，每一回合都珍贵"},
    "晚期":  {"diff": 2.6, "desc": "已发生转移，必须靠联合治疗"},
}

ROOMS = ["骨髓", "淋巴结", "脾脏", "肝脏", "肺", "血液", "肠道"]

# 细胞：名字 -> (招募费, 攻击, 生命, 特性说明)
CELLS = {
    "T细胞":     (3, 6, 6,  "主力，稳定输出"),
    "NK细胞":    (3, 8, 4,  "高爆发，专杀逃逸细胞"),
    "巨噬细胞":  (2, 4, 9,  "肉盾，吞噬残骸回血"),
    "树突细胞":  (4, 3, 5,  "标记目标，全队伤害+1"),
    "中性粒细胞":(1, 7, 2,  "短命高攻，一回合后凋亡"),
    "B细胞":     (3, 5, 5,  "抗体，跨器官支援"),
    "细胞因子":  (2, 0, 4,  "辅助：全队攻击+2、器官好感+3"),
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

# 药物：名字 -> (价格, 效果说明, 效果键)
DRUGS = {
    "化疗药物":         (2, "对全场癌细胞造成 6 伤害，副作用：全器官好感-2", "chemo"),
    "靶向药物":         (3, "对指定器官造成 10 伤害", "targeted"),
    "免疫检查点抑制剂": (4, "全队攻击+3，持续 2 回合", "checkpoint"),
    "放疗":             (3, "清除一个器官内 60% 癌细胞，好感-3", "radio"),
    "CAR-T疗法":        (6, "召唤一支顶级 T 细胞战队（攻12/血10）", "cart"),
    "BRCA-RNA疫苗":     (5, "本回合敌方增殖停止，且胜利点+3", "vaccine"),
    "激素疗法":         (2, "降低全局 BOSS 强度 30%", "hormone"),
    "阿司匹林":         (1, "解除当前负面状态", "aspirin"),
    "手术":             (5, "直接削减一个器官 50% 癌细胞，好感-5", "surgery"),
}


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
        # 两种资源分家（这是最要紧的结构改动）：
        #   antibody  = 抗体，用来招兵/买药（花掉）
        #   remission = 缓解度，只在"把器官照顾好"时增长（评分/通关依据）
        # 之前两者合一时，"招兵"会直接推远通关目标，和"不招兵会死"互相打架。
        self.antibody = 6
        self.remission = 0
        self.escaped = 0
        self.endless = False
        self.boss_mult = 1.0
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

    # ── 器官驻军：按器官偏好生成，减少逐个 random 分支 ────────────────
    def _make_garrison(self, room):
        prefer = ROOM_PREFERENCE[room]
        troops = []
        for _ in range(self.rng.randint(2, 3)):
            name = self.rng.choice(prefer if self.rng.random() < 0.75 else list(CELLS))
            atk, hp = CELLS[name][1], CELLS[name][2]
            troops.append({"name": name, "atk": atk, "hp": hp})
        return troops

    # ── 输出 ─────────────────────────────────────────────────────────
    def say(self, text=""):
        print(text, flush=True)

    def banner(self):
        self.say("=" * 58)
        self.say("  抗癌文字冒险游戏 · 免疫系统指挥官（重写版 v2）")
        self.say("=" * 58)
        self.say(f"  开局模式：{self.mode} —— {MODES[self.mode]['desc']}")
        self.say(f"  目标：累计胜利点 ≥ {MAX_VICTORY_POINTS}；生命 {self.lives} 条")

    def status(self):
        avg_fall = sum(r["fall"] for r in self.rooms.values()) / len(self.rooms)
        self.say(f"\n【第 {self.round} 回合】生命{self.lives}  抗体{self.antibody}"
                 f"  缓解度{self.remission}/{MAX_VICTORY_POINTS}"
                 f"  逃逸{self.escaped}  平均沦陷{avg_fall:.0f}%  部队{len(self.cells)}")
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
        return f"招募 {name}（-{cost} 抗体），入驻{where}。"

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
            return "化疗药物：全场癌细胞 -6，各器官好感 -2。"
        if eff == "targeted":
            d["cancer"] = max(0, d["cancer"] - 10)
            return f"靶向药物：{room} 癌细胞 -10。"
        if eff == "checkpoint":
            self.checkpoint_turns = 2
            return "免疫检查点抑制剂：全队攻击 +3（2 回合）。"
        if eff == "radio":
            cut = int(d["cancer"] * 0.6)
            d["cancer"] -= cut
            d["favor"] = max(20, d["favor"] - 3)
            return f"放疗：{room} 清除 {cut} 癌细胞，好感 -3。"
        if eff == "cart":
            self.cells.append({"name": "T细胞", "atk": 12, "hp": 10})
            return "CAR-T疗法：一支顶级 T 细胞战队加入（攻12/血10）。"
        if eff == "vaccine":
            self.victory += 3
            d["cancer"] = max(0, d["cancer"] - 2)
            return "BRCA-RNA疫苗：本回合增殖停止，胜利点 +3。"
        if eff == "hormone":
            self.hormone_turns = 3
            return "激素疗法：BOSS 强度 -30%（3 回合）。"
        if eff == "aspirin":
            self.hormone_turns = 0
            return "阿司匹林：负面状态已解除。"
        if eff == "surgery":
            cut = int(d["cancer"] * 0.5)
            d["cancer"] -= cut
            d["favor"] = max(20, d["favor"] - 5)
            return f"手术：{room} 癌细胞 -{cut}，好感 -5。"
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
            d = self.rooms[target]
            d["cancer"] = max(0, d["cancer"] - dmg)
            killed += dmg
            if c["name"] == "中性粒细胞":
                self.cells.remove(c)          # 一回合后凋亡
            elif c["name"] == "巨噬细胞" and c["hp"] < CELLS["巨噬细胞"][2]:
                c["hp"] += 1
        return killed

    def enemy_turn(self):
        grew = 0
        for r in ROOMS:
            d = self.rooms[r]
            # 难度倍率必须作用在**主循环**上，否则"晚期"和"20岁"打起来一样
            # （原先 self.diff 只用在 boss_turn，等于难度模式形同虚设）
            growth = 1 + int(self.diff)       # 20岁 +2，晚期 +3
            if d["favor"] < 40:               # 器官好感低 → 癌细胞更容易扩散
                growth += 1
            d["cancer"] += growth
            # ── favor/fall 正式进主循环（招牌机制得真的影响胜负）──
            if d["favor"] >= 70:
                d["cancer"] = max(0, d["cancer"] - 2)        # 器官自己清理
            elif d["favor"] < 30 and d["garrison"]:
                d["garrison"].pop(0)                          # 微环境恶化，驻军凋亡
            # 癌细胞越多器官越撑不住 → 好感下滑（放任就成恶性循环）
            d["favor"] = max(0, d["favor"] - d["cancer"] // 8)
            grew += 1
            d["fall"] = min(100, int(d["cancer"] * 1.5 + (80 - d["favor"])))
        # 逃逸：某个器官彻底沦陷时播散
        for r in ROOMS:
            if self.rooms[r]["fall"] >= 100:
                self.escaped += self.rng.randint(1, 3)
        if self.checkpoint_turns:
            self.checkpoint_turns -= 1
        if self.hormone_turns:
            self.hormone_turns -= 1
        if self.rng.random() < 0.25:
            self.say("  ⚠ 血栓事件：血流受阻，本回合招募费 +1。")
            return grew + 1
        return grew

    def boss_turn(self):
        mult = self.boss_mult * (0.7 if self.hormone_turns else 1.0)
        # 比例伤害 + 上限：原来无上限递增（实测"受 137 点压制"），后期只剩挨打。
        # 现在最多打掉当前部队的一半，boss_mult 也封顶。
        dmg = int(5 * mult * self.diff)
        if self.cells:
            hit = max(1, len(self.cells) // 2)
            victims = self.rng.sample(self.cells, min(hit, len(self.cells)))
            self.say(f"  ☠ BOSS 回合：肿瘤负荷冲击 {dmg} 点，{len(victims)} 支部队承压。")
            for victim in victims:
                victim["hp"] -= dmg
                if victim["hp"] <= 0:
                    self.say(f"     {victim['name']} 阵亡。")
                    self.cells.remove(victim)
        else:
            self.lives -= 1
            self.say("     战队全灭，免疫系统被迫重启。")
        self.boss_mult = min(3.0, self.boss_mult + 0.15)

    def rebirth(self):
        self.lives -= 1
        if self.lives <= 0:
            return False
        self.say("\n💫 重生：骨髓深处的造血干细胞开始疯狂增殖，")
        self.say("   新一批免疫细胞正在诞生——但记忆细胞仍记得敌人的样子。")
        # 重生必须**发一队新细胞**，否则空战队会每回合扣命（死亡螺旋）
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
        # 抗体收入：照顾得好的器官反过来给你补给（让 favor 变成经济命脉）
        wealthy = sum(1 for r in self.rooms.values() if r["favor"] >= 70)
        self.antibody += 1 + wealthy // 3
        killed = self.battle()
        self.enemy_turn()
        # 缓解度只认**持续维持**的"和谐"：器官既要干净、又要好感够高。
        # （早先按"干净器官个数"每回合直接加，7 个器官能加 10/回合 → 2 回合就通关）
        healthy = sum(1 for r in self.rooms.values()
                      if r["cancer"] <= 2 and r["favor"] >= 60)
        if healthy >= 4:                       # 过半器官进入和谐状态
            self.remission += 1
        if healthy == len(ROOMS):              # 全身和谐额外 +1
            self.remission += 1
        if self.round % BOSS_INTERVAL == 0:
            self.boss_turn()
        if self.remission >= ENDLESS_MILESTONES[0] // 8 and not self.endless:
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
        return killed

    # ── 交互 ─────────────────────────────────────────────────────────
    def menu(self, forced=None):
        self.say("\n可选行动： 1招募 2用药 3推进回合 4查看战队 5结束")
        raw = forced if forced is not None else self._read("> ")
        return (raw or "").strip().lower()

    def _read(self, prompt):
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            return "5"          # 输入断了就按"结束"处理，宿主关管道时不会卡住

    def play(self, scripted=None):
        self.banner()
        scripted = list(scripted or [])
        while True:
            self.status()
            choice = self.menu(scripted.pop(0) if scripted else None)
            if choice in ("5", "q", "quit", "exit"):
                self.say("指挥结束。免疫系统记住了这场战争。")
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
            # 默认推进回合
            result = self.tick()
            if result == "dead":
                self.say("\n☠ 生命耗尽。这场战争没能赢下来。")
                return "dead"
            if result == "win":
                self.say(f"\n🎉 缓解！第 {self.round} 回合达成 {MAX_VICTORY_POINTS} 缓解度。")
                self.say("   真正的健康不是征服，而是和谐。")
                return "win"
            if result == "metastasis":
                self.say("\n☠ 逃逸的癌细胞发生转移，免疫系统失去控制。")
                return "metastasis"
            if result == "timeout":
                self.say(f"\n⌛ 打到 {MAX_ROUNDS} 回合仍未分胜负，战争进入长期共存。")
                return "timeout"


def run_selftest():
    """确定性脚本流程：从 20 岁开局，逐步演练各条路径后退出（不依赖人输入）。"""
    print("[selftest] 用固定种子跑一遍确定性流程")
    g = Game("20岁", seed=20261013)
    scripted = ["4",                    # 查看战队
                "1", "T细胞",           # 招募
                "2", "化疗药物", "肝脏", # 用药
                "3", "3", "3", "3", "3",  # 推进 5 回合（会撞上 BOSS）
                "1", "巨噬细胞",
                "2", "疫苗" , "脾脏",
                "3", "3", "3", "3", "3",
                "5"]                    # 主动结束
    result = g.play(scripted=scripted)
    print(f"[selftest] 结果={result} 回合={g.round} 抗体={g.antibody} "
          f"缓解度={g.remission} 生命={g.lives} 部队={len(g.cells)} 逃逸={g.escaped}")
    # 另外单测几个纯逻辑分支（不经过菜单）
    g2 = Game("晚期", seed=7)
    g2.antibody = 12
    msg = g2.recruit("T细胞")
    assert g2.antibody == 9, "招募应当花掉抗体（12-3=9）"
    g2.drugs["手术"] = 1
    g2.use_drug("手术", "肺")
    assert g2.antibody == 4, "用药也应当花抗体（9-5=4）"
    g2.tick()
    assert g2.round == 1, "回合应当推进到 1"
    d_room = g2.rooms["肺"]
    assert d_room["fall"] >= 0 and d_room["favor"] <= 100, "器官数据应当在合理区间"
    print(f"[selftest] 分支断言通过（{msg}；肺 好感{d_room['favor']} "
          f"沦陷{d_room['fall']}%；抗体{g2.antibody}）")
    ok = result in ("quit", "win", "timeout")
    print("[selftest] " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    if selftest_mode():
        return run_selftest()
    print("选择开局：")
    for i, m in enumerate(MODES, 1):
        print(f"  {i}. {m} —— {MODES[m]['desc']}")
    try:
        pick = input("编号（回车=1）：").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        pick = "1"
    keys = list(MODES)
    mode = keys[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(keys) else "20岁"
    Game(mode).play()
    return 0


if __name__ == "__main__":
    sys.exit(main())
