import React, { useMemo } from 'react';
import { Responsive, WidthProvider, Layout, Layouts as ReactGridLayouts } from 'react-grid-layout';
import { Box, Paper } from '@mui/material';
import { Layouts, ViewModeState, GridItem as GridItemType, LayoutItem } from '../../types';
import { VIEW_MODE_LAYOUTS, VIEW_MODE_COLS } from '../../constants';
import { GridItem } from './GridItem';
import { usePluginStudio } from '../../hooks';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';

const ResponsiveGridLayout = WidthProvider(Responsive);

interface GridContainerProps {
  layouts: Layouts | null;
  onLayoutChange: (layout: Layout[], newLayouts: Layouts, metadata?: { origin?: { source?: string } }) => void;
  onResizeStart?: () => void;
  onResizeStop?: () => void;
  viewMode: ViewModeState;
  viewWidth: number;
  newItemId?: string | null;
  canvasWidth: number;
  canvasHeight: number;
  zoom?: number;
}

/**
 * Component that renders the responsive grid layout
 * @param props The component props
 * @returns The grid container component
 */
export const GridContainer: React.FC<GridContainerProps> = ({
  layouts,
  onLayoutChange,
  onResizeStart,
  onResizeStop,
  viewMode,
  viewWidth,
  newItemId = null,
  canvasWidth,
  canvasHeight,
  zoom = 1
}) => {
  const { selectedItem, setSelectedItem, previewMode } = usePluginStudio();
  const interactionSourceRef = React.useRef<'user-drag' | 'user-resize' | 'drop-add' | null>(null);
  const interactionResetTimeoutRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const effectiveWidth = (viewMode.type === 'desktop' || viewMode.type === 'custom') ? canvasWidth : viewWidth;
  const scaledWidth = effectiveWidth * zoom;
  const scaledHeight = Math.max(400, canvasHeight) * zoom;

  React.useEffect(() => {
    // Inform react-grid-layout that available width changed so it recalculates column sizes
    window.dispatchEvent(new Event('resize'));
  }, [scaledWidth]);

  const cancelInteractionReset = React.useCallback(() => {
    if (interactionResetTimeoutRef.current) {
      clearTimeout(interactionResetTimeoutRef.current);
      interactionResetTimeoutRef.current = null;
    }
  }, []);

  const scheduleInteractionReset = React.useCallback((delay = 150) => {
    cancelInteractionReset();
    interactionResetTimeoutRef.current = setTimeout(() => {
      interactionSourceRef.current = null;
      interactionResetTimeoutRef.current = null;
    }, delay);
  }, [cancelInteractionReset]);

  const handleDragStart = React.useCallback(() => {
    cancelInteractionReset();
    interactionSourceRef.current = 'user-drag';
  }, [cancelInteractionReset]);

  const handleDragStop = React.useCallback(() => {
    scheduleInteractionReset();
  }, [scheduleInteractionReset]);

  const handleResizeStartInternal = React.useCallback(() => {
    cancelInteractionReset();
    interactionSourceRef.current = 'user-resize';
    onResizeStart?.();
  }, [cancelInteractionReset, onResizeStart]);

  const handleResizeStopInternal = React.useCallback(() => {
    onResizeStop?.();
    scheduleInteractionReset();
  }, [onResizeStop, scheduleInteractionReset]);

  React.useEffect(() => {
    if (newItemId) {
      cancelInteractionReset();
      interactionSourceRef.current = 'drop-add';
      scheduleInteractionReset(200);
    }
  }, [newItemId, cancelInteractionReset, scheduleInteractionReset]);

  React.useEffect(() => {
    return () => {
      if (interactionResetTimeoutRef.current) {
        clearTimeout(interactionResetTimeoutRef.current);
      }
    };
  }, []);
  
  /**
   * Handle item selection
   * @param itemId The ID of the item to select
   */
  const handleItemSelect = (itemId: string) => {
    setSelectedItem({ i: itemId });
  };
  
  // If no layouts, show empty state
  if (!layouts) {
    return (
      <Paper
        elevation={0}
        sx={{
          p: 4,
          border: '2px dashed rgba(0, 0, 0, 0.1)',
          borderRadius: 2,
          minHeight: 400,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center'
        }}
      >
        <Box sx={{ textAlign: 'center', color: 'text.secondary' }}>
          Drag and drop modules from the toolbar to add them to the canvas
        </Box>
      </Paper>
    );
  }
  
  // Get the current layout based on view mode
  const currentViewType = viewMode.type === 'custom' ? 'desktop' : viewMode.type;
  const currentLayout = layouts?.[currentViewType] || [];
  
  // Get the current view mode config
  const currentViewModeConfig = VIEW_MODE_LAYOUTS[viewMode.type];
  
  // Convert our Layouts type to ReactGridLayout.Layouts type and memoize it
  const convertedLayouts: ReactGridLayouts = useMemo(() => {
    if (!layouts) {
      return { desktop: [], tablet: [], mobile: [] };
    }
    
    // Return the layouts directly - React Grid Layout will handle them properly
    return {
      desktop: layouts.desktop || [],
      tablet: layouts.tablet || [],
      mobile: layouts.mobile || []
    };
  }, [layouts]);
  
  // Memoize the layout change handler to prevent unnecessary re-renders
  const handleLayoutChange = React.useCallback((currentLayout: Layout[], allLayouts: ReactGridLayouts) => {
    const convertLayoutArray = (candidateLayouts: Layout[] | undefined, currentLayouts: (GridItemType | LayoutItem)[] | undefined): (GridItemType | LayoutItem)[] => {
      if (!candidateLayouts) return [];

      return candidateLayouts.map(layout => {
        const originalItem = currentLayouts?.find(item => item.i === layout.i);

        if (originalItem) {
          if (originalItem.x === layout.x &&
              originalItem.y === layout.y &&
              originalItem.w === layout.w &&
              originalItem.h === layout.h) {
            return originalItem;
          }

          return {
            ...originalItem,
            x: layout.x,
            y: layout.y,
            w: layout.w,
            h: layout.h
          };
        }

        return {
          moduleUniqueId: layout.i,
          i: layout.i,
          x: layout.x,
          y: layout.y,
          w: layout.w,
          h: layout.h
        } as LayoutItem;
      });
    };

    const ourLayouts: Layouts = {
      desktop: convertLayoutArray(allLayouts.desktop, layouts?.desktop),
      tablet: convertLayoutArray(allLayouts.tablet, layouts?.tablet),
      mobile: convertLayoutArray(allLayouts.mobile, layouts?.mobile)
    };

    const originSource = interactionSourceRef.current;
    const metadata = originSource ? { origin: { source: originSource } } : undefined;

    onLayoutChange(currentLayout, ourLayouts, metadata);
  }, [layouts, onLayoutChange]);
  
  return (
    <Box
      sx={{
        minHeight: scaledHeight,
        width: scaledWidth,
        maxWidth: 'none',
        mx: 'auto',
      }}
    >
      <Box
        sx={{
          transform: `scale(${zoom})`,
          transformOrigin: 'top left',
          width: effectiveWidth,
          minHeight: Math.max(400, canvasHeight),
        }}
      >
        <ResponsiveGridLayout
          className="layout"
          layouts={convertedLayouts}
          breakpoints={{
            desktop: VIEW_MODE_LAYOUTS.desktop.cols,
            tablet: VIEW_MODE_LAYOUTS.tablet.cols,
            mobile: VIEW_MODE_LAYOUTS.mobile.cols
          }}
          cols={{
            desktop: VIEW_MODE_COLS.desktop,
            tablet: VIEW_MODE_COLS.tablet,
            mobile: VIEW_MODE_COLS.mobile
          }}
          rowHeight={currentViewModeConfig.rowHeight}
          margin={currentViewModeConfig.margin}
          containerPadding={currentViewModeConfig.padding}
          onLayoutChange={handleLayoutChange}
          onResizeStart={handleResizeStartInternal}
          onResizeStop={handleResizeStopInternal}
          onDragStart={handleDragStart}
          onDragStop={handleDragStop}
          isDraggable={!previewMode}
          isResizable={!previewMode}
          compactType="vertical"
          useCSSTransforms={true}
          draggableHandle=".react-grid-dragHandleExample"
          preventCollision={false}
          allowOverlap={false}
          measureBeforeMount={false}
          transformScale={zoom}
        >
        {currentLayout
          .filter(item => item && item.i && typeof item.y === 'number' && typeof item.x === 'number' &&
                  typeof item.w === 'number' && typeof item.h === 'number') // Filter out invalid items and ensure all required properties exist
          .map(item => {
            // Ensure item has pluginId (required by GridItem)
            const gridItem = 'pluginId' in item ?
              item as GridItemType :
              { ...item, pluginId: '' } as GridItemType;
              
            return (
              <div key={item.i}>
                <GridItem
                  item={gridItem}
                  isSelected={selectedItem?.i === item.i}
                  onSelect={() => handleItemSelect(item.i)}
                  previewMode={previewMode}
                  isNew={item.i === newItemId}
                />
              </div>
            );
          })}
        </ResponsiveGridLayout>
      </Box>
    </Box>
  );
};
