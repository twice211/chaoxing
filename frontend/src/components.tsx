import {
  useEffect,
  useRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    study: (
      <>
        <path d="M3 5h7l2 2 2-2h7v15h-7l-2 1-2-1H3z" />
        <path d="M12 7v14M6 9h3M15 9h3" />
      </>
    ),
    exercise: (
      <>
        <path d="M8 4h8l1 2h3v15H4V6h3zM8 4v4h8V4" />
        <path d="m8 14 2 2 6-6" />
      </>
    ),
    grades: (
      <>
        <path d="M4 20h16M6 17v-5M12 17V5M18 17V9" />
      </>
    ),
    discuss: (
      <>
        <path d="M3 4h18v13H9l-6 4z" />
        <path d="M7 8h10M7 12h6" />
      </>
    ),
    search: (
      <>
        <circle cx="10" cy="10" r="6" />
        <path d="m15 15 6 6" />
      </>
    ),
    wrong: (
      <>
        <path d="M5 3h14v18H5zM9 3v18" />
        <path d="m12 8 4 4m0-4-4 4M12 16h4" />
      </>
    ),
    settings: (
      <>
        <path d="M4 7h16M4 17h16" />
        <circle cx="9" cy="7" r="3" />
        <circle cx="15" cy="17" r="3" />
      </>
    ),
    play: <path d="m8 4 12 8-12 8z" />,
    pause: (
      <>
        <path d="M8 5v14M16 5v14" />
      </>
    ),
    check: <path d="m5 12 4 4 10-10" />,
    refresh: (
      <>
        <path d="M20 10a8 8 0 1 0-2 8M20 3v7h-7" />
      </>
    ),
    arrow: <path d="m9 5 7 7-7 7" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    login: (
      <>
        <path d="M14 3h7v18h-7M3 12h12m-4-4 4 4-4 4" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] ?? paths.study}
    </svg>
  );
}
export function Button({
  children,
  tone = "normal",
  icon,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: "normal" | "primary" | "danger" | "quiet";
  icon?: string;
}) {
  return (
    <button className={`button ${tone}`} {...props}>
      {icon && <Icon name={icon} size={17} />}
      <span>{children}</span>
    </button>
  );
}
export function Panel({
  title,
  description,
  children,
  className = "",
  action,
}: {
  title?: string;
  description?: string;
  children: ReactNode;
  className?: string;
  action?: ReactNode;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-head">
          <div>
            <h2>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function Empty({
  icon = "study",
  title,
  children,
}: {
  icon?: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon name={icon} size={28} />
      </div>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Toggle({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="toggle-label">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        disabled={disabled}
      />
      <span className="toggle-track" />
      <span>{label}</span>
    </label>
  );
}
export function Modal({
  title,
  children,
  onClose,
  footer,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  footer: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement;
    const modal = ref.current;
    modal?.querySelector<HTMLElement>("button, input")?.focus();
    function keyboard(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
      }
      if (event.key !== "Tab" || !modal) return;
      const focusable = [
        ...modal.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
        ),
      ];
      const first = focusable[0],
        last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
    document.addEventListener("keydown", keyboard);
    return () => {
      document.removeEventListener("keydown", keyboard);
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, []);
  return (
    <div className="modal-backdrop">
      <div
        className="modal"
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="panel-head">
          <h2>{title}</h2>
          <button className="icon-button" aria-label="关闭" onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        <div className="modal-footer">{footer}</div>
      </div>
    </div>
  );
}
