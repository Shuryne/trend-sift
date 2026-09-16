import { useEffect, useId, useRef, useState } from "react";
import {
  CalendarDays,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";

const isoDay = (date: Date) => date.toISOString().slice(0, 10);
function shiftMonth(month: string, offset: number) {
  const date = new Date(`${month}-01T00:00:00Z`);
  date.setUTCMonth(date.getUTCMonth() + offset);
  return isoDay(date).slice(0, 7);
}

export default function DateSelect({
  value,
  dates,
  currentDate,
  onChange,
}: {
  value: string;
  dates: string[];
  currentDate: string;
  onChange: (value: string) => void;
}) {
  const today = isoDay(new Date());
  const anchor =
    /^\d{4}-\d{2}-\d{2}$/.test(currentDate) &&
    !Number.isNaN(Date.parse(`${currentDate}T00:00:00Z`))
      ? currentDate
      : today;
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState(anchor.slice(0, 7));
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const id = useId();
  const available = new Set(dates);
  const months = [
    ...new Set([...dates, anchor].map((date) => date.slice(0, 7))),
  ].sort();
  const years = [
    ...new Set([...months, month].map((date) => date.slice(0, 4))),
  ];
  const first = new Date(`${month}-01T00:00:00Z`);
  const start = (first.getUTCDay() + 6) % 7;
  const daysInMonth = new Date(
    Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0),
  ).getUTCDate();

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    const selected =
      popup.current?.querySelector<HTMLButtonElement>(
        'button[aria-pressed="true"]:not(:disabled)',
      ) ||
      popup.current?.querySelector<HTMLButtonElement>(
        ".calendar-day:not(:disabled)",
      );
    (
      selected || popup.current?.querySelector<HTMLButtonElement>("button")
    )?.focus();
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  function choose(date: string) {
    onChange(date);
    setOpen(false);
    trigger.current?.focus();
  }

  return (
    <div
      className="date-picker"
      ref={root}
      onBlur={(event) => {
        if (
          event.relatedTarget &&
          !event.currentTarget.contains(event.relatedTarget)
        )
          setOpen(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.preventDefault();
          setOpen(false);
          trigger.current?.focus();
        }
      }}
    >
      <button
        ref={trigger}
        className="date-control"
        aria-label={`归档日期：${value || `最新${currentDate ? ` · ${currentDate}` : "归档"}`}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => {
          setMonth(anchor.slice(0, 7));
          setOpen(!open);
        }}
      >
        <CalendarDays size={14} />
        <span>日期</span>
        <span className="date-value">
          {value ||
            (currentDate ? `最新 · ${currentDate.slice(5)}` : "最新归档")}
        </span>
        <ChevronDown size={14} />
      </button>
      {open && (
        <div
          className="calendar-popover"
          ref={popup}
          id={id}
          role="dialog"
          aria-label="选择归档日期"
        >
          <button className="calendar-latest" onClick={() => choose("")}>
            最新归档<span>{dates[0] || "暂无归档"}</span>
          </button>
          <div className="calendar-month">
            <button
              aria-label="上个月"
              disabled={month <= months[0]}
              onClick={() => setMonth(shiftMonth(month, -1))}
            >
              <ChevronLeft size={16} />
            </button>
            <div className="calendar-month-selects">
              <select
                aria-label="年份"
                value={month.slice(0, 4)}
                onChange={(event) => {
                  const matching = months.filter((item) =>
                    item.startsWith(event.target.value),
                  );
                  const target = `${event.target.value}-${month.slice(5)}`;
                  setMonth(matching.includes(target) ? target : matching[0]);
                }}
              >
                {years.map((year) => (
                  <option key={year} value={year}>
                    {year} 年
                  </option>
                ))}
              </select>
              <select
                aria-label="月份"
                value={month.slice(5)}
                onChange={(event) =>
                  setMonth(`${month.slice(0, 4)}-${event.target.value}`)
                }
              >
                {Array.from({ length: 12 }, (_, index) =>
                  String(index + 1).padStart(2, "0"),
                ).map((item) => (
                  <option
                    key={item}
                    value={item}
                    disabled={!months.includes(`${month.slice(0, 4)}-${item}`)}
                  >
                    {Number(item)} 月
                  </option>
                ))}
              </select>
            </div>
            <button
              aria-label="下个月"
              disabled={month >= months[months.length - 1]}
              onClick={() => setMonth(shiftMonth(month, 1))}
            >
              <ChevronRight size={16} />
            </button>
          </div>
          <div className="calendar-grid">
            {["一", "二", "三", "四", "五", "六", "日"].map((day) => (
              <span className="calendar-weekday" key={day}>
                {day}
              </span>
            ))}
            {Array.from({ length: 42 }, (_, index) => {
              const day = index - start + 1;
              if (day < 1 || day > daysInMonth) return <span key={index} />;
              const date = `${month}-${String(day).padStart(2, "0")}`;
              return (
                <button
                  key={index}
                  className="calendar-day"
                  disabled={!available.has(date)}
                  aria-label={`${date}${date === today ? "，今天" : ""}${!available.has(date) ? "，无归档" : ""}`}
                  aria-pressed={date === currentDate}
                  aria-current={date === today ? "date" : undefined}
                  onClick={() => choose(date)}
                >
                  {day}
                </button>
              );
            })}
          </div>
          <p className="calendar-hint">
            灰色日期暂无归档 · 圆点标记今天（UTC）
          </p>
        </div>
      )}
    </div>
  );
}
