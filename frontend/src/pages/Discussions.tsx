import { useState } from "react";
import { Button, Empty, Modal, Panel } from "../components";
import { eventText } from "../state";
import type { BackendEvent, RunCommand, Snapshot } from "../types";
export function Discussions({
  snapshot,
  events,
  run,
  disabled,
}: {
  snapshot: Snapshot | null;
  events: BackendEvent[];
  run: RunCommand;
  disabled: boolean;
}) {
  const [kind, setKind] = useState("new_post");
  const [max, setMax] = useState("5");
  const [error, setError] = useState("");
  const [notPublished, setNotPublished] = useState(false);
  const result = [...events]
    .reverse()
    .find(
      (event) =>
        event.level ===
        (kind === "new_post" ? "discuss_post" : "discuss_reply"),
    );
  const recommendation = [...events]
    .reverse()
    .find((event) => event.level === "discuss_rec");
  async function setMaximum() {
    const count = Number(max);
    if (!Number.isInteger(count) || count < 1 || count > 50) {
      setError("每轮数量应为 1–50 的整数。");
      return;
    }
    setError("");
    await run("discuss_set_max", { value: count });
  }
  const locked = disabled || !snapshot?.course_id;
  return (
    <div className="page-stack">
      <Panel
        title="讨论草稿"
        description="先预览内容、填入平台，再核对实际发布结果。"
      >
        <div className="segmented" aria-label="讨论类型">
          <button
            className={kind === "new_post" ? "active" : ""}
            aria-pressed={kind === "new_post"}
            onClick={() => setKind("new_post")}
          >
            发表主题
          </button>
          <button
            className={kind === "reply" ? "active" : ""}
            aria-pressed={kind === "reply"}
            onClick={() => setKind("reply")}
          >
            回复话题
          </button>
        </div>
        <div className="button-row">
          <Button
            tone="primary"
            disabled={locked}
            onClick={() => void run("discuss_preview", { kind })}
          >
            预览草稿
          </Button>
          <Button
            disabled={locked}
            onClick={() => void run("discuss_fill_next", { kind })}
          >
            填入下一条
          </Button>
          <Button
            disabled={locked}
            onClick={() => void run("discuss_auto", { kind })}
          >
            开始本轮讨论
          </Button>
        </div>
        {result ? (
          <pre className="result-text draft">{eventText(result)}</pre>
        ) : (
          <Empty icon="discuss" title="草稿确认，从预览开始">
            内容生成后在这里核对；自动讨论会在发布前要求确认本轮完整内容。
          </Empty>
        )}
        <div className="panel-bottom button-row">
          <Button
            disabled={locked}
            onClick={() => void run("discuss_confirm", { kind })}
          >
            确认已发布
          </Button>
          <Button
            disabled={locked}
            onClick={() => void run("discuss_discard", { kind })}
          >
            丢弃当前草稿
          </Button>
          <Button
            tone="quiet"
            disabled={locked}
            onClick={() => setNotPublished(true)}
          >
            核实未发布
          </Button>
        </div>
      </Panel>
      <Panel
        title="本轮数量"
        description="讨论相关的生成与提交开关可在设置中调整。"
      >
        <div className="inline-form">
          <label>
            最多处理（条）
            <input
              type="number"
              min="1"
              max="50"
              step="1"
              value={max}
              onChange={(e) => setMax(e.target.value)}
            />
          </label>
          <Button disabled={locked} onClick={() => void setMaximum()}>
            应用数量
          </Button>
        </div>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        {recommendation && (
          <p className="quiet-note">{eventText(recommendation)}</p>
        )}
      </Panel>
      {notPublished && (
        <Modal
          title="核实发布结果"
          onClose={() => setNotPublished(false)}
          footer={
            <>
              <Button onClick={() => setNotPublished(false)}>取消</Button>
              <Button
                tone="primary"
                disabled={locked}
                onClick={() => {
                  void run("discuss_not_published", { kind }).then((ok) => {
                    if (ok) setNotPublished(false);
                  });
                }}
              >
                确认确实未发布
              </Button>
            </>
          }
        >
          <p>
            请先检查平台中的目标话题，确认这一条确实没有发布。确认后才能重新填入或提交，避免重复发布。
          </p>
        </Modal>
      )}
    </div>
  );
}
