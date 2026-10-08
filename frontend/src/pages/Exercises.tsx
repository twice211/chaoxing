import { useState } from "react";
import { Button, Panel, Toggle } from "../components";
import type { RunCommand, Section, Snapshot } from "../types";
export function Exercises({
  snapshot,
  selected,
  run,
  disabled,
}: {
  snapshot: Snapshot | null;
  selected?: Section;
  run: RunCommand;
  disabled: boolean;
}) {
  const [limit, setLimit] = useState("5");
  const [skipDone, setSkipDone] = useState(true);
  const [restart, setRestart] = useState(false);
  const [fromSelected, setFromSelected] = useState(false);
  const [error, setError] = useState("");
  async function chain() {
    const count = Number(limit);
    if (!Number.isInteger(count) || count < 1 || count > 50) {
      setError("连做节数应为 1–50 的整数。");
      return;
    }
    if (fromSelected && !selected) {
      setError("请先从目录选择起始小节。");
      return;
    }
    setError("");
    await run("auto_chain", {
      limit: count,
      skip_done: skipDone,
      restart,
      start_id: fromSelected ? selected?.id : null,
    });
  }
  const settings = snapshot?.settings.values;
  return (
    <div className="page-stack">
      <Panel
        title="章节检测"
        description={
          selected ? selected.title : "先从目录选择小节，再进入章节检测。"
        }
      >
        <div className="step-guide">
          <span>进入检测</span>
          <IconArrow />
          <span>解析本页</span>
          <IconArrow />
          <span>检查并作答</span>
        </div>
        <div className="button-row">
          <Button
            tone="primary"
            disabled={disabled || !selected}
            onClick={() => void run("task_tab")}
          >
            进入检测
          </Button>
          <Button
            disabled={disabled || !selected}
            onClick={() => void run("parse")}
          >
            解析本页
          </Button>
          <Button
            disabled={disabled || !selected}
            onClick={() =>
              void run("auto", {
                submit: Boolean(settings?.PRACTICE_ALLOW_SUBMIT),
              })
            }
          >
            自动作答
          </Button>
          <Button
            disabled={disabled || !selected}
            onClick={() => void run("dump")}
          >
            导出本页结构
          </Button>
        </div>
        <p className="quiet-note">
          自动作答遵循练习模式和已保存的提交设置。真实考试仍由后端保护。
        </p>
      </Panel>
      <Panel
        title="连续练习"
        description="按课程目录推进，停止后可以从已记录的进度继续。"
      >
        <label className="field compact">
          连做节数
          <input
            type="number"
            min="1"
            max="50"
            step="1"
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
          />
        </label>
        <div className="option-stack">
          <Toggle
            label="跳过已完成的小节"
            checked={skipDone}
            onChange={setSkipDone}
          />
          <Toggle
            label="从头重新开始"
            checked={restart}
            onChange={setRestart}
          />
          <Toggle
            label="从选中小节开始"
            checked={fromSelected}
            onChange={setFromSelected}
          />
        </div>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="button-row">
          <Button
            tone="primary"
            disabled={disabled || !snapshot?.course_id}
            onClick={() => void chain()}
          >
            开始连做
          </Button>
          <Button
            tone="danger"
            disabled={!snapshot?.ready}
            onClick={() => void run("stop")}
          >
            停止作答
          </Button>
        </div>
      </Panel>
      <div className="inline-notice">
        当前答完动作：
        <strong>
          {settings?.PRACTICE_SUBMIT_ACTION === "save"
            ? "暂时保存"
            : "按配置提交"}
        </strong>
        。练习开关、自动提交和重试可在设置中调整。
      </div>
    </div>
  );
}
function IconArrow() {
  return (
    <span className="muted" aria-hidden="true">
      ›
    </span>
  );
}
