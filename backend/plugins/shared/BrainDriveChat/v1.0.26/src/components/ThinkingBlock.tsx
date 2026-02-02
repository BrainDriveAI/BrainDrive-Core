import React from 'react';

interface ThinkingBlockProps {
  children: string;
  isStreaming?: boolean;
}

interface ThinkingBlockState {
  isExpanded: boolean;
  ellipsisStage: number;
}

class ThinkingBlock extends React.Component<ThinkingBlockProps, ThinkingBlockState> {
  private ellipsisTimer: number | null = null;

  constructor(props: ThinkingBlockProps) {
    super(props);
    this.state = {
      isExpanded: false,
      ellipsisStage: -1
    };
  }

  componentDidMount() {
    if (this.props.isStreaming) {
      this.setState({ ellipsisStage: 0 }, this.startEllipsisLoop);
    }
  }

  componentDidUpdate(prevProps: ThinkingBlockProps) {
    // Collapse when a new streaming sequence begins
    if (!prevProps.isStreaming && this.props.isStreaming) {
      this.setState({ isExpanded: false, ellipsisStage: 0 }, this.startEllipsisLoop);
    }

    if (prevProps.isStreaming && !this.props.isStreaming) {
      this.stopEllipsisLoop(true);
    }
  }

  componentWillUnmount() {
    this.stopEllipsisLoop(false);
  }

  toggleExpanded = () => {
    this.setState(prevState => ({
      isExpanded: !prevState.isExpanded
    }));
  };

  private startEllipsisLoop = () => {
    if (this.ellipsisTimer !== null) {
      return;
    }

    this.ellipsisTimer = window.setInterval(() => {
      this.setState(prevState => ({
        ellipsisStage: prevState.ellipsisStage >= 0
          ? (prevState.ellipsisStage + 1) % 3
          : 0
      }));
    }, 500);
  };

  private stopEllipsisLoop = (resetStage: boolean) => {
    if (this.ellipsisTimer !== null) {
      window.clearInterval(this.ellipsisTimer);
      this.ellipsisTimer = null;
    }

    if (resetStage && this.state.ellipsisStage !== -1) {
      this.setState({ ellipsisStage: -1 });
    }
  };

  render() {
    const { children, isStreaming } = this.props;
    const { isExpanded, ellipsisStage } = this.state;
    
    // Remove the <think> and </think> tags from the content
    const content = children.replace(/<\/?think>/g, '').trim();

    const showEllipsis = isStreaming && ellipsisStage >= 0;
    const ellipsisText = showEllipsis ? ` ${'.'.repeat(ellipsisStage + 1)}` : '';
    const titleText = `Thinking${ellipsisText}`;

    return (
      <div className={`thinking-block ${isExpanded ? 'expanded' : 'collapsed'} ${isStreaming ? 'thinking-streaming' : ''}`}>
        <button
          type="button"
          className="thinking-header"
          onClick={this.toggleExpanded}
          aria-expanded={isExpanded}
        >
          <span className="thinking-title">{titleText}</span>
          <span className="thinking-toggle" aria-hidden="true">
            {isExpanded ? '▼' : '▶'}
          </span>
        </button>
        <div className="thinking-panel" aria-hidden={!isExpanded}>
          {isExpanded && (
            <div className="thinking-content">
              {content}
            </div>
          )}
        </div>
      </div>
    );
  }
}

export default ThinkingBlock;
