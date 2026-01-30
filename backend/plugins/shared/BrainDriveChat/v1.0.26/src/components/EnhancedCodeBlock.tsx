import React from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';

interface EnhancedCodeBlockProps {
  children: string;
  className?: string;
  language?: string;
  key?: string;
}

interface CodeBlockState {
  isCollapsed: boolean;
  isCopied: boolean;
}

class EnhancedCodeBlock extends React.Component<EnhancedCodeBlockProps, CodeBlockState> {
  private contentHash: string;

  constructor(props: EnhancedCodeBlockProps) {
    super(props);
    const lines = props.children.split('\n');
    this.contentHash = this.generateContentHash(props.children);
    this.state = {
      isCollapsed: false, // Don't collapse by default
      isCopied: false
    };
    console.log(`🔧 EnhancedCodeBlock created with hash: ${this.contentHash.substring(0, 8)}, ${lines.length} lines, collapsible: ${lines.length > 3}`);
  }

  componentDidMount() {
    console.log(`🔧 EnhancedCodeBlock mounted, isCollapsed: ${this.state.isCollapsed}`);
  }

  componentDidUpdate(prevProps: EnhancedCodeBlockProps, prevState: CodeBlockState) {
    if (prevState.isCollapsed !== this.state.isCollapsed) {
      console.log(`🔧 EnhancedCodeBlock collapse state changed: ${prevState.isCollapsed} -> ${this.state.isCollapsed}`);
    }
  }

  // Generate a simple hash for the content to help with debugging
  private generateContentHash(content: string): string {
    let hash = 0;
    for (let i = 0; i < content.length; i++) {
      const char = content.charCodeAt(i);
      hash = ((hash << 5) - hash) + char;
      hash = hash & hash; // Convert to 32-bit integer
    }
    return Math.abs(hash).toString(16);
  }

  // Extract language from className (format: language-{lang})
  get detectedLanguage() {
    return this.props.language || (this.props.className?.match(/language-(\w+)/)?.[1] || 'text');
  }
  
  // Determine if code block should be collapsible (more than 3 lines)
  get lines() {
    return this.props.children.split('\n');
  }

  get isCollapsible() {
    return this.lines.length > 3;
  }

  get displayLines() {
    return this.state.isCollapsed ? this.lines.slice(0, 3) : this.lines;
  }

  get displayContent() {
    return this.displayLines.join('\n');
  }

  /**
   * Copy code to clipboard
   */
  copyToClipboard = async () => {
    try {
      await navigator.clipboard.writeText(this.props.children);
      this.setState({ isCopied: true });
      
      // Reset copied state after 2 seconds
      setTimeout(() => {
        this.setState({ isCopied: false });
      }, 2000);
    } catch (error) {
      console.error('Failed to copy code:', error);
    }
  };

  /**
   * Toggle collapse/expand
   */
  toggleCollapse = () => {
    console.log(`🔧 EnhancedCodeBlock toggleCollapse called, current state: ${this.state.isCollapsed}`);
    this.setState(prevState => {
      const newState = !prevState.isCollapsed;
      console.log(`🔧 EnhancedCodeBlock setting isCollapsed to: ${newState}`);
      return { isCollapsed: newState };
    });
  };

  render() {
    const { className } = this.props;
    const { isCollapsed, isCopied } = this.state;

    return (
      <div className="enhanced-code-block">
        {/* Code block header */}
        <div className="code-block-header">
          <div className="code-block-info">
            <span className="code-language">{this.detectedLanguage}</span>
            <span className="code-lines">{this.lines.length} line{this.lines.length !== 1 ? 's' : ''}</span>
          </div>
          <div className="code-block-actions">
            {this.isCollapsible && (
              <button
                onClick={this.toggleCollapse}
                className="code-action-btn"
                title={isCollapsed ? "Expand code" : "Collapse code"}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  {isCollapsed ? (
                    <polyline points="6,9 12,15 18,9"></polyline>
                  ) : (
                    <polyline points="18,15 12,9 6,15"></polyline>
                  )}
                </svg>
              </button>
            )}
            <button
              onClick={this.copyToClipboard}
              className={`code-action-btn ${isCopied ? 'copied' : ''}`}
              title={isCopied ? "Copied!" : "Copy code"}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                {isCopied ? (
                  <polyline points="20,6 9,17 4,12"></polyline>
                ) : (
                  <>
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                  </>
                )}
              </svg>
            </button>
          </div>
        </div>
        
                            {/* Code content */}
                    <div className={`code-block-content ${isCollapsed ? 'collapsed' : ''}`}>
                      <SyntaxHighlighter
                        language={this.detectedLanguage}
                        style={oneDark}
                        customStyle={{
                          margin: 0,
                          borderRadius: 0,
                          fontSize: '0.875rem',
                          lineHeight: 1.5,
                          fontFamily: "'Monaco', 'Menlo', 'Ubuntu Mono', monospace"
                        }}
                        showLineNumbers={this.lines.length > 1}
                        wrapLines={true}
                        lineNumberStyle={{
                          minWidth: '2.5em',
                          paddingRight: '1em',
                          textAlign: 'right',
                          userSelect: 'none',
                          color: '#6c757d'
                        }}
                      >
                        {this.displayContent}
                      </SyntaxHighlighter>
                      {isCollapsed && (
                        <button 
                          className="code-collapse-indicator"
                          onClick={this.toggleCollapse}
                          title="Click to expand"
                        >
                          <span>... {this.lines.length - 3} more lines</span>
                        </button>
                      )}
                    </div>
      </div>
    );
  }
}

export default EnhancedCodeBlock;
