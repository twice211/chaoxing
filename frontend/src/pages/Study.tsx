import { useState } from "react";
import { Button, Empty, Icon, Panel, Toggle } from "../components";
import type { BackendEvent, RunCommand, Section, Snapshot } from "../types";
import { eventText, progressPercent } from "../state";

export function Study({
  snapshot,
  selected,
  run,
  disabled,
  events,
}: {
  snapshot: Snapshot | null;
  selected?: Section;
  run: RunCommand;
  disabled: boolean;
  events: BackendEvent[];
}) {
  const [seconds, setSeconds] = useState("");
  const [autoNext, setAutoNext] = useState(false);
  const [validation, setValidation] = useState("");
  const playStatus = [...events]
    .reverse()
    .find((event) => event.level === "playstatus");
  const reading =
    selected && /read|doc|text|pdf|阅读|文档/i.test(selected.kind);
  const progress = selected ? progressPercent(selected.progress) : 0;
  async function read() {
    const value = seconds.trim() === "" ? null : Number(seconds);
    if (value !== null && (!Number.isInteger(value) || value < 0)) {
      setValidation("阅读时长请输入非负整数秒，留空使用配置。");
      return;
    }
    setValidation("");
    if (selected)
      await run("read", { item_id: selected.id, min_seconds: value });
  }
  return (
    <div className="page-stack">
      <Panel
        className="study-panel"
        title="小节工作台"
        action={
          <span className="badge">{selected ? selected.kind : "等待选择"}</span>
        }
      >
        {selected ? (
          <>
            <div className="lesson-title">
              <span className="lesson-mark">
                <Icon name={reading ? "study" : "play"} size={25} />
              </span>
              <div>
                <p className="muted">当前小节</p>
                <h3>{selected.title}</h3>
              </div>
            </div>
            <div className="lesson-stage">
              <div className="stage-symbol">
                <Icon
                  name={selected.done ? "check" : reading ? "study" : "play"}
                  size={40}
                />
              </div>
              <h3>
                {selected.done
                  ? "本节已完成"
                  : reading
                    ? "准备开始阅读"
                    : "准备好，开始本节学习"}
              </h3>
              <p>
                {playStatus
                  ? eventText(playStatus)
                  : "学习内容将在关联浏览器中打开，运行状态在这里同步。"}
              </p>
            </div>
            <div className="progress-label">
              <span>{selected.done ? "已完成" : "小节进度"}</span>
              <strong>{progress.toFixed(0)}%</strong>
            </div>
            <div className="progress-track">
              <span style={{ width: `${progress}%` }} />
            </div>
            <div className="button-row">
              <Button
                tone="primary"
                icon="play"
                disabled={disabled}
                onClick={() => void run("play", { item_id: selected.id })}
              >
                播放本节
              </Button>
              <Button
                icon="pause"
                disabled={!snapshot?.ready}
                onClick={() => void run("pause")}
              >
                暂停
              </Button>
              <Button
                disabled={!snapshot?.ready}
                onClick={() => void run("resume")}
              >
                继续播放
              </Button>
              <Button
                disabled={!snapshot?.ready}
                onClick={() => void run("stop_play")}
              >
                停止
              </Button>
            </div>
          </>
        ) : (
          <Empty title="从目录选择一个小节">
            登录后读取课程和目录，视频与阅读内容会出现在这里。
          </Empty>
        )}
        <div className="panel-bottom">
          <Toggle
            label="完成后自动进入下一节"
            checked={autoNext}
            disabled={!snapshot?.ready}
            onChange={(value) => {
              void run("auto_next", { on: value }).then((ok) => {
                if (ok) setAutoNext(value);
              });
            }}
          />
        </div>
      </Panel>
      <Panel
        title="定时阅读"
        description="适用于阅读和文档小节；留空沿用已保存的阅读时长。"
      >
        <div className="inline-form">
          <label>
            阅读时长（秒）
            <input
              type="number"
              min="0"
              step="1"
              placeholder="使用配置"
              value={seconds}
              onChange={(e) => setSeconds(e.target.value)}
            />
          </label>
          <Button disabled={disabled || !selected} onClick={() => void read()}>
            开始阅读
          </Button>
          <Button
            disabled={!snapshot?.ready}
            onClick={() => void run("stop_play")}
          >
            停止阅读
          </Button>
        </div>
        {validation && (
          <p className="form-error" role="alert">
            {validation}
          </p>
        )}
      </Panel>
    </div>
  );
}
