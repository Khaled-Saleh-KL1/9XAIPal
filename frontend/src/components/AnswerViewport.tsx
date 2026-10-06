import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';

const BOTTOM_THRESHOLD = 24;

function answerHeightLimit(): number {
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
  return window.matchMedia('(max-width: 640px)').matches
    ? viewportHeight * 0.55
    : Math.min(viewportHeight * 0.6, 560);
}

export function AnswerViewport({
  children,
  streaming = false,
  dir = 'auto',
  onScrollableChange,
}: {
  children: ReactNode;
  streaming?: boolean;
  dir?: 'ltr' | 'rtl' | 'auto';
  onScrollableChange?: (scrollable: boolean) => void;
}) {
  const viewportId = useId();
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const followLatestRef = useRef(true);
  const scrollableRef = useRef(false);
  const expandedRef = useRef(false);
  const streamingRef = useRef(streaming);
  const onScrollableChangeRef = useRef(onScrollableChange);
  const hasMeasuredOverflowRef = useRef(false);
  const [scrollable, setScrollable] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [atBottom, setAtBottom] = useState(true);
  const [scrolledFromTop, setScrolledFromTop] = useState(false);
  const [hasMoreBelow, setHasMoreBelow] = useState(false);

  streamingRef.current = streaming;
  onScrollableChangeRef.current = onScrollableChange;
  scrollableRef.current = scrollable;
  expandedRef.current = expanded;

  const measureOverflow = useCallback(() => {
    const content = contentRef.current;
    const nextScrollable = !!content && content.scrollHeight > answerHeightLimit() + 1;
    const changed = !hasMeasuredOverflowRef.current || scrollableRef.current !== nextScrollable;
    hasMeasuredOverflowRef.current = true;
    scrollableRef.current = nextScrollable;
    setScrollable((current) => current === nextScrollable ? current : nextScrollable);
    if (changed) onScrollableChangeRef.current?.(nextScrollable);
    return nextScrollable;
  }, []);

  const syncIndicators = useCallback((trackFollow: boolean) => {
    const scroll = scrollRef.current;
    if (!scroll || !scrollableRef.current || expandedRef.current) {
      setAtBottom(true);
      setScrolledFromTop(false);
      setHasMoreBelow(false);
      return;
    }

    const remaining = Math.max(0, scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop);
    const nextAtBottom = remaining <= BOTTOM_THRESHOLD;
    if (trackFollow) followLatestRef.current = nextAtBottom;
    setAtBottom(nextAtBottom);
    setScrolledFromTop(scroll.scrollTop > 2);
    setHasMoreBelow(remaining > 2);
  }, []);

  const jumpToBottom = useCallback(() => {
    const scroll = scrollRef.current;
    if (!scroll) return;
    followLatestRef.current = true;
    scroll.scrollTop = scroll.scrollHeight;
    syncIndicators(false);
  }, [syncIndicators]);

  const refreshLayout = useCallback(() => {
    const overflowing = measureOverflow();
    if (overflowing && streamingRef.current && !expandedRef.current && followLatestRef.current) {
      jumpToBottom();
    } else {
      syncIndicators(false);
    }
  }, [jumpToBottom, measureOverflow, syncIndicators]);

  useLayoutEffect(() => {
    const overflowing = measureOverflow();
    const scroll = scrollRef.current;
    if (!overflowing || !scroll) {
      syncIndicators(false);
      return;
    }

    // The next layout pass applies the scroll cap. Run again after that class
    // lands so the assignment targets this answer's scroll region only.
    if (!scrollable) return;

    if (streaming && !expanded && followLatestRef.current) {
      jumpToBottom();
    } else {
      syncIndicators(false);
    }
  }, [children, expanded, jumpToBottom, measureOverflow, scrollable, streaming, syncIndicators]);

  useEffect(() => {
    const content = contentRef.current;
    const scroll = scrollRef.current;
    const observer = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(refreshLayout);
    if (content) observer?.observe(content);
    if (scroll) observer?.observe(scroll);
    window.addEventListener('resize', refreshLayout);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', refreshLayout);
    };
  }, [refreshLayout]);

  const onScroll = () => syncIndicators(true);
  const toggleExpanded = () => setExpanded((value) => !value);
  const clipped = scrollable && !expanded;

  return (
    <div
      className={`answer-viewport${clipped ? ' is-clipped' : ''}${clipped && scrolledFromTop ? ' has-top-fade' : ''}${clipped && hasMoreBelow ? ' has-bottom-fade' : ''}`}
      data-testid="answer-viewport"
    >
      <div className={`answer-scroll-frame${clipped ? ' is-clipped' : ''}${clipped && scrolledFromTop ? ' has-top-fade' : ''}${clipped && hasMoreBelow ? ' has-bottom-fade' : ''}`}>
        <div
          id={viewportId}
          ref={scrollRef}
          dir={dir}
          className={`answer-scroll${clipped ? ' is-scrollable' : ''}${expanded ? ' is-expanded' : ''}`}
          data-answer-scroll
          onScroll={onScroll}
        >
          <div className="answer-scroll-content" data-answer-content ref={contentRef}>
            {children}
          </div>
        </div>

        {clipped && streaming && !atBottom && (
          <button
            type="button"
            className="answer-jump"
            aria-controls={viewportId}
            onClick={jumpToBottom}
          >
            Jump to latest
          </button>
        )}
      </div>

      {scrollable && (
        <div className="answer-controls" dir={dir}>
          <button
            type="button"
            className="answer-expand"
            aria-controls={viewportId}
            aria-expanded={expanded}
            onClick={toggleExpanded}
          >
            {expanded ? 'Collapse' : 'Expand'}
          </button>
        </div>
      )}
    </div>
  );
}
