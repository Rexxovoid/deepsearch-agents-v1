import { useCallback, useEffect, useRef, useState } from "react";
import type { UIEvent, WheelEvent } from "react";

/** Keep following only while the reader stays at the bottom of this scroll pane. */
export function useFollowLatest<T extends HTMLElement>(content: unknown, sessionKey?: string) {
  const ref = useRef<T | null>(null);
  const followingRef = useRef(true);
  const [isFollowing, setIsFollowing] = useState(true);

  const setFollowing = useCallback((following: boolean) => {
    followingRef.current = following;
    setIsFollowing(following);
  }, []);

  const resumeFollowing = useCallback(() => {
    setFollowing(true);
    const node = ref.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [setFollowing]);

  useEffect(() => {
    setFollowing(true);
  }, [sessionKey, setFollowing]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const node = ref.current;
      // Re-check when the callback runs: the user may have scrolled after it
      // was scheduled. Avoid smooth-scroll animations fighting with the wheel.
      if (node && followingRef.current) node.scrollTop = node.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [content, sessionKey]);

  const onScroll = useCallback((event: UIEvent<T>) => {
    const node = event.currentTarget;
    setFollowing(node.scrollHeight - node.clientHeight - node.scrollTop <= 8);
  }, [setFollowing]);

  const onWheel = useCallback((event: WheelEvent<T>) => {
    if (event.deltaY < 0) setFollowing(false);
  }, [setFollowing]);

  return { ref, onScroll, onWheel, isFollowing, resumeFollowing };
}
