/**
 * useAnimation Hook - Unified Dynamic Page Renderer
 * 
 * React hook for managing CSS-based animations with performance monitoring
 * and reduced motion support.
 */

import { useRef, useEffect, useCallback, useState } from 'react';
import {
  AnimationConfig,
  AnimationState,
  UseAnimationOptions,
  UseAnimationReturn,
  AnimationEvent,
  AnimationEventType
} from '../types/animation';
import { animationService } from '../services/AnimationService';

export function useAnimation(
  initialConfig?: AnimationConfig,
  options: UseAnimationOptions = {}
): UseAnimationReturn {
  const elementRef = useRef<HTMLElement | null>(null);
  const [animationState, setAnimationState] = useState<AnimationState | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [progress, setProgress] = useState(0);
  const currentAnimationId = useRef<string | null>(null);

  const {
    autoPlay = false,
    respectReducedMotion = true,
    enablePerformanceMonitoring = true,
    onComplete,
    onStart
  } = options;

  // Update animation state
  const updateAnimationState = useCallback(() => {
    if (currentAnimationId.current) {
      const state = animationService.getAnimationState(currentAnimationId.current);
      if (state) {
        setAnimationState(state);
        setIsPlaying(state.status === 'running');
        setIsPaused(state.status === 'paused');
        setProgress(state.progress);
      }
    }
  }, []);

  // Event handlers
  const handleAnimationStart = useCallback((event: AnimationEvent) => {
    if (!currentAnimationId.current) {
      currentAnimationId.current = event.animationId;
    }
    if (event.animationId === currentAnimationId.current) {
      setIsPlaying(true);
      setIsPaused(false);
      if (onStart) onStart();
    }
  }, [onStart]);

  const handleAnimationEnd = useCallback((event: AnimationEvent) => {
    if (event.animationId === currentAnimationId.current) {
      setIsPlaying(false);
      setIsPaused(false);
      setProgress(1);
      if (onComplete) onComplete();
    }
  }, [onComplete]);

  const handleAnimationPause = useCallback((event: AnimationEvent) => {
    if (event.animationId === currentAnimationId.current) {
      setIsPaused(true);
      setIsPlaying(false);
    }
  }, []);

  const handleAnimationResume = useCallback((event: AnimationEvent) => {
    if (event.animationId === currentAnimationId.current) {
      setIsPaused(false);
      setIsPlaying(true);
    }
  }, []);

  const handleAnimationCancel = useCallback((event: AnimationEvent) => {
    if (event.animationId === currentAnimationId.current) {
      setIsPlaying(false);
      setIsPaused(false);
      setProgress(0);
      currentAnimationId.current = null;
    }
  }, []);

  // Setup event listeners
  useEffect(() => {
    const eventTypes: AnimationEventType[] = ['start', 'end', 'pause', 'resume', 'cancel'];
    const handlers = [
      handleAnimationStart,
      handleAnimationEnd,
      handleAnimationPause,
      handleAnimationResume,
      handleAnimationCancel
    ];

    eventTypes.forEach((type, index) => {
      animationService.addEventListener(type, handlers[index]);
    });

    return () => {
      eventTypes.forEach((type, index) => {
        animationService.removeEventListener(type, handlers[index]);
      });
    };
  }, [
    handleAnimationStart,
    handleAnimationEnd,
    handleAnimationPause,
    handleAnimationResume,
    handleAnimationCancel
  ]);

  // Configure reduced motion and performance monitoring
  useEffect(() => {
    if (respectReducedMotion) {
      animationService.setReducedMotionConfig({
        respectUserPreference: true,
        fallbackDuration: 200,
        disableAnimations: false,
        alternativeEffects: {
          fade: true,
          scale: false,
          position: false
        }
      });
    }

    if (enablePerformanceMonitoring) {
      animationService.setPerformanceConfig({
        enableMonitoring: true,
        targetFrameRate: 60,
        jankThreshold: 16.67,
        memoryThreshold: 50 * 1024 * 1024,
        reportingInterval: 1000
      });
    }
  }, [respectReducedMotion, enablePerformanceMonitoring]);

  // Auto-play animation if configured
  useEffect(() => {
    if (autoPlay && initialConfig && elementRef.current && !currentAnimationId.current) {
      play(initialConfig);
    }
  }, [autoPlay, initialConfig]);

  // Animation control methods
  const play = useCallback(async (config?: Partial<AnimationConfig>): Promise<void> => {
    if (!elementRef.current) {
      throw new Error('Element ref is not set. Make sure to attach the ref to a DOM element.');
    }

    // Stop current animation if running
    if (currentAnimationId.current) {
      animationService.stop(currentAnimationId.current);
    }

    // Merge config with initial config
    const finalConfig: AnimationConfig = {
      name: 'fadeIn',
      duration: 300,
      easing: 'ease-out',
      ...initialConfig,
      ...config
    };

    try {
      await animationService.play(finalConfig, elementRef.current);
    } catch (error) {
      console.error('Animation failed:', error);
      throw error;
    }
  }, [initialConfig]);

  const pause = useCallback(() => {
    if (currentAnimationId.current) {
      animationService.pause(currentAnimationId.current);
    }
  }, []);

  const resume = useCallback(() => {
    if (currentAnimationId.current) {
      animationService.resume(currentAnimationId.current);
    }
  }, []);

  const stop = useCallback(() => {
    if (currentAnimationId.current) {
      animationService.stop(currentAnimationId.current);
      currentAnimationId.current = null;
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (currentAnimationId.current) {
        animationService.stop(currentAnimationId.current);
      }
    };
  }, []);

  return {
    play,
    pause,
    resume,
    stop,
    state: animationState,
    isPlaying,
    isPaused,
    progress,
    ref: elementRef
  };
}

export default useAnimation;
