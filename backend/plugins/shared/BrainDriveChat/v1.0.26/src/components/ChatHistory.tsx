import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChatMessage, SearchResult } from '../types';
import { formatTimestamp } from '../utils';
import EnhancedCodeBlock from './EnhancedCodeBlock';
import ThinkingBlock from './ThinkingBlock';
import {
    EditIcon,
    RegenerateIcon,
    ContinueIcon,
    SearchIcon,
    ScrollToBottomIcon,
    MarkdownToggleIcon
  } from '../icons';

const KEYBOARD_SCROLL_KEYS = new Set<string>([
  'ArrowUp',
  'ArrowDown',
  'PageUp',
  'PageDown',
  'Home',
  'End',
  'Space'
]);

type ScrollIntentSource = 'pointer' | 'wheel' | 'touch' | 'key';

interface ChatHistoryProps {
  messages: ChatMessage[];
  isLoading: boolean;
  isLoadingHistory: boolean;
  error: string;
  chatHistoryRef: React.RefObject<HTMLDivElement>;
  editingMessageId: string | null;
  editingContent: string;
  onStartEditing: (messageId: string, content: string) => void;
  onCancelEditing: () => void;
  onSaveEditing: () => void;
  onEditingContentChange: (content: string) => void;
  onRegenerateResponse: () => void;
  onContinueGeneration: () => void;
  showScrollToBottom?: boolean;
  onScrollToBottom?: () => void;
  onToggleMarkdown?: (messageId: string) => void;
  onScroll?: (event: React.UIEvent<HTMLDivElement>) => void;
  onUserScrollIntent?: (source: ScrollIntentSource) => void;
}

interface ChatHistoryState {
  expandedSearchResults: Set<string>;
  expandedDocumentContext: Set<string>;
  expandedRetrievedContext: Set<string>;
}

class ChatHistory extends React.Component<ChatHistoryProps, ChatHistoryState> {
  constructor(props: ChatHistoryProps) {
    super(props);
    this.state = {
      expandedSearchResults: new Set(),
      expandedDocumentContext: new Set(),
      expandedRetrievedContext: new Set(),
    };
  }

  private renderRetrievedContextBlock = (messageId: string, retrievalData: NonNullable<ChatMessage['retrievalData']>) => {
    const isExpanded = this.state.expandedRetrievedContext.has(messageId);
    const chunkCount = retrievalData.chunks?.length || 0;
    const collectionLabel = retrievalData.collectionName || 'Selected collection';

    return (
      <div className={`retrieved-context-block ${isExpanded ? 'expanded' : 'collapsed'}`}>
        <button
          type="button"
          className="retrieved-context-header-btn"
          onClick={() => this.toggleRetrievedContext(messageId)}
          aria-expanded={isExpanded}
        >
          <span className="retrieved-context-title-row">
            <span className="retrieved-icon">📚</span>
            <span className="retrieved-collection">{collectionLabel}</span>
            <span className="retrieved-info">
              {chunkCount} chunk{chunkCount === 1 ? '' : 's'}
              {retrievalData.intent?.type ? ` • ${retrievalData.intent.type}` : ''}
            </span>
          </span>
          <span className="retrieved-context-toggle" aria-hidden="true">
            {isExpanded ? '▼' : '▶'}
          </span>
        </button>

        <div className="retrieved-context-panel" aria-hidden={!isExpanded}>
          {isExpanded && (
            <div className="retrieved-context-panel-inner">
              <div className="retrieved-chunks">
                {retrievalData.chunks.map((chunk: any, index: number) => {
                  const filename = chunk?.metadata?.document_filename || chunk?.metadata?.original_filename || '';
                  return (
                    <div key={chunk.id || index} className="retrieved-chunk-item">
                      <div className="retrieved-chunk-header">
                        <span className="retrieved-chunk-number">#{index + 1}</span>
                        {filename && <span className="retrieved-chunk-filename">{filename}</span>}
                      </div>
                      <div className="retrieved-chunk-content">{chunk.content}</div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  };

  emitScrollIntent = (source: ScrollIntentSource) => {
    if (this.props.onUserScrollIntent) {
      this.props.onUserScrollIntent(source);
    }
  };

  handlePointerDown = () => {
    this.emitScrollIntent('pointer');
  };

  handleWheel = () => {
    this.emitScrollIntent('wheel');
  };

  handleTouchStart = () => {
    this.emitScrollIntent('touch');
  };

  handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (KEYBOARD_SCROLL_KEYS.has(event.key)) {
      this.emitScrollIntent('key');
    }
  };

  /**
   * Toggle search results expansion
   */
  toggleSearchResults = (messageId: string) => {
    this.setState(prevState => {
      const newSet = new Set(prevState.expandedSearchResults);
      if (newSet.has(messageId)) {
        newSet.delete(messageId);
      } else {
        newSet.add(messageId);
      }
      return { expandedSearchResults: newSet };
    });
  };

  /**
   * Toggle document context expansion
   */
  toggleDocumentContext = (messageId: string) => {
    this.setState(prevState => {
      const newSet = new Set(prevState.expandedDocumentContext);
      if (newSet.has(messageId)) {
        newSet.delete(messageId);
      } else {
        newSet.add(messageId);
      }
      return { expandedDocumentContext: newSet };
    });
  };

  /**
   * Toggle retrieved context expansion
   */
  toggleRetrievedContext = (messageId: string) => {
    this.setState(prevState => {
      const newSet = new Set(prevState.expandedRetrievedContext);
      if (newSet.has(messageId)) {
        newSet.delete(messageId);
      } else {
        newSet.add(messageId);
      }
      return { expandedRetrievedContext: newSet };
    });
  };

  /**
   * Render search result item
   */
  renderSearchResult = (result: SearchResult, index: number) => {
    return (
      <div key={index} className="search-result-item">
        <div className="search-result-header">
          <span className="search-result-number">{index + 1}.</span>
          <a 
            href={result.url} 
            target="_blank" 
            rel="noopener noreferrer"
            className="search-result-title"
          >
            {result.title}
          </a>
        </div>
        <div className="search-result-url">{result.url}</div>
        {result.content && (
          <div className="search-result-content">{result.content}</div>
        )}
      </div>
    );
  };

  /**
   * Render document context message
   */
  renderDocumentContext = (message: ChatMessage) => {
    const { documentData } = message;
    if (!documentData) return null;

    const isExpanded = this.state.expandedDocumentContext.has(message.id);
    const { results } = documentData;
    const filename = documentData.filename || (results && results.length === 1 ? results[0].filename : undefined) || 'Document';
    const segmentCount = documentData.segmentCount || (results ? results.length : undefined);
    const totalChars = documentData.totalChars || (results && results[0] ? results[0].text_length : undefined);

    return (
      <div key={message.id} className="message message-ai message-document-context">
        <div className="message-bubble">
          <div className="message-body">
            <div className="document-context-header">
              <div className="document-context-summary">
                <span className="document-icon">📄</span>
                <span className="document-filename">
                  {filename}
                </span>
                <span className="document-info">
                  {segmentCount ? `${segmentCount} segment${segmentCount > 1 ? 's' : ''}` : ''}
                  {totalChars ? ` • ${totalChars} chars` : ''}
                  {documentData.truncated ? ' • truncated' : ''}
                  {documentData.mode ? ` • ${documentData.mode}` : ''}
                </span>
              </div>
              <button
                onClick={() => this.toggleDocumentContext(message.id)}
                className="document-toggle-btn"
                title={isExpanded ? "Hide document content" : "Show document content"}
              >
                <span className="document-toggle-text">
                  {isExpanded ? "Hide" : "Show"}
                </span>
                <span className={`document-toggle-icon ${isExpanded ? 'expanded' : ''}`}>
                  {isExpanded ? '▼' : '▶'}
                </span>
              </button>
            </div>
            
            {isExpanded && (
              <div className="document-context-content">
                <div className="document-content">
                  {documentData.context}
                </div>
              </div>
            )}
          </div>
          <div className="message-meta">
            <span className="message-timestamp">{formatTimestamp(message.timestamp)}</span>
          </div>
        </div>
      </div>
    );
  };

  /**
   * Render retrieved context (RAG chunks) message
   */
  renderRetrievedContextMessage = (message: ChatMessage) => {
    const { retrievalData } = message;
    if (!retrievalData) return null;

    return (
      <div key={message.id} className="message message-ai message-retrieved-context">
        <div className="message-bubble">
          <div className="message-body">
            {this.renderRetrievedContextBlock(message.id, retrievalData)}
          </div>
          <div className="message-meta">
            <span className="message-timestamp">{formatTimestamp(message.timestamp)}</span>
          </div>
        </div>
      </div>
    );
  };



  /**
   * Render search results message
   */
  renderSearchResultsMessage = (message: ChatMessage) => {
    const { searchData } = message;
    if (!searchData) return null;

    const isExpanded = this.state.expandedSearchResults.has(message.id);
    const hasScrapedContent = searchData.scrapedContent && searchData.successfulScrapes && searchData.successfulScrapes > 0;

    return (
      <div key={message.id} className="message message-ai message-search-results">
        <div className="message-bubble">
          <div className="message-body">
            <div className="search-results-header">
              <div className="search-results-summary">
                <span className="search-icon"><SearchIcon /></span>
                <span className="search-query">"{searchData.query}"</span>
                <span className="search-count">({searchData.totalResults} results)</span>
                {hasScrapedContent && (
                  <span className="scraping-info">
                    + {searchData.successfulScrapes} pages scraped
                  </span>
                )}
              </div>
              <button
                className="search-toggle-btn"
                onClick={() => this.toggleSearchResults(message.id)}
                aria-label={isExpanded ? "Collapse search results" : "Expand search results"}
              >
                <span className="search-toggle-icon">
                  {isExpanded ? '▼' : '▶'}
                </span>
                <span className="search-toggle-text">
                  {isExpanded ? 'Hide sources' : 'Show sources'}
                </span>
              </button>
            </div>
            
            {isExpanded && (
              <div className="search-results-content">
                <div className="search-results-list">
                  {searchData.results.map((result, index) => this.renderSearchResult(result, index))}
                </div>
                {hasScrapedContent && (
                  <div className="scraping-summary">
                    <div className="scraping-header">
                      <span className="scraping-icon">📄</span>
                      <span>Detailed content scraped from {searchData.successfulScrapes} web page(s)</span>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="message-meta">
            <span className="message-timestamp">{formatTimestamp(message.timestamp)}</span>
          </div>
        </div>
      </div>
    );
  };

  /**
   * Render a chat message
   */
  renderMessage = (message: ChatMessage) => {
    // Handle search results messages separately
    if (message.isSearchResults) {
      return this.renderSearchResultsMessage(message);
    }

    // Handle document context messages separately
    if (message.isDocumentContext) {
      return this.renderDocumentContext(message);
    }

    // Handle retrieved context messages separately
    if (message.isRetrievedContext) {
      return this.renderRetrievedContextMessage(message);
    }

    // Handle thinking tags in content
    const { sender, content, timestamp, isStreaming, isEditable, isEdited, canRegenerate, canContinue, showRawMarkdown } = message;

    const THINK_OPEN_TAG = '<think>';
    const THINK_CLOSE_TAG = '</think>';
    const thinkStartIndex = content.indexOf(THINK_OPEN_TAG);

    let displayContent = content;
    let thinkingContent: string | null = null;

    if (thinkStartIndex !== -1) {
      const beforeThink = content.slice(0, thinkStartIndex);
      const afterOpenTag = content.slice(thinkStartIndex + THINK_OPEN_TAG.length);
      const thinkEndIndex = afterOpenTag.indexOf(THINK_CLOSE_TAG);

      if (thinkEndIndex !== -1) {
        thinkingContent = afterOpenTag.slice(0, thinkEndIndex);
        const afterThink = afterOpenTag.slice(thinkEndIndex + THINK_CLOSE_TAG.length);
        displayContent = `${beforeThink}${afterThink}`;
      } else {
        thinkingContent = afterOpenTag;
        displayContent = beforeThink;
      }

      displayContent = displayContent.trim();
      if (thinkingContent !== null) {
        thinkingContent = thinkingContent.trim();
      }
    }

    const isEditing = this.props.editingMessageId === message.id;
    const messageClassNames = [
      'message',
      `message-${sender}`,
      isStreaming ? 'message-streaming' : '',
      isEditing ? 'message-is-editing' : '',
      showRawMarkdown ? 'message-raw' : ''
    ].filter(Boolean).join(' ');

    const showRetrievedContextInline = sender === 'ai' && !!message.retrievalData && !message.isRetrievedContext;

    let mainContent: React.ReactNode;
    if (isEditing) {
      mainContent = (
        <div className="message-edit-container">
          <textarea
            value={this.props.editingContent}
            onChange={(e) => this.props.onEditingContentChange(e.target.value)}
            className="message-edit-input"
            rows={Math.max(1, this.props.editingContent.split('\n').length)}
            autoFocus
          />
          <div className="message-edit-actions">
            <button
              onClick={this.props.onSaveEditing}
              className="btn btn-primary btn-sm"
              disabled={!this.props.editingContent.trim()}
            >
              Save
            </button>
            <button
              onClick={this.props.onCancelEditing}
              className="btn btn-secondary btn-sm"
            >
              Cancel
            </button>
          </div>
        </div>
      );
    } else if (sender === 'user') {
      mainContent = (
        <div className="message-text user-message-text">
          {displayContent}
        </div>
      );
    } else if (showRawMarkdown) {
      mainContent = (
        <pre className="raw-markdown-content">
          <code>{displayContent}</code>
        </pre>
      );
    } else {
      mainContent = (
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ node, ...props }) => (
              <a
                {...props}
                target="_blank"
                rel="noopener noreferrer"
                className="markdown-link"
                style={{ color: 'inherit' }}
              />
            ),
            code: ({ node, inline, className, children, ...props }: any) => {
              const match = /language-(\w+)/.exec(className || '');
              const codeContent = String(children).replace(/\n$/, '');

              if (!inline && (codeContent.includes('\n') || className)) {
                const codeBlockKey = `${message.id}-${codeContent.slice(0, 50).replace(/\s/g, '')}-${codeContent.length}`;
                return (
                  <EnhancedCodeBlock
                    key={codeBlockKey}
                    className={className}
                    language={match?.[1]}
                  >
                    {codeContent}
                  </EnhancedCodeBlock>
                );
              }

              return (
                <code className="markdown-inline-code" {...props}>
                  {codeContent}
                </code>
              );
            },
            blockquote: ({ node, ...props }) => (
              <blockquote className="markdown-blockquote" {...props} />
            ),
            table: ({ node, ...props }) => (
              <div className="markdown-table-wrapper">
                <table className="markdown-table" {...props} />
              </div>
            ),
            th: ({ node, ...props }) => (
              <th className="markdown-table-header" {...props} />
            ),
            td: ({ node, ...props }) => (
              <td className="markdown-table-cell" {...props} />
            ),
          }}
        >
          {displayContent}
        </ReactMarkdown>
      );
    }

    const userAction = sender === 'user' && isEditable && !isEditing ? (
      <button
        onClick={() => this.props.onStartEditing(message.id, content)}
        className="message-action-btn"
        title="Edit message"
      >
        <EditIcon />
      </button>
    ) : null;

    const aiActions = sender === 'ai' && !isStreaming ? (
      <div className="ai-message-actions">
        <button
          onClick={() => this.props.onToggleMarkdown?.(message.id)}
          className="message-action-btn"
          title={showRawMarkdown ? "Show formatted view" : "Show raw markdown"}
        >
          <MarkdownToggleIcon />
        </button>
        {canRegenerate && (
          <button
            onClick={this.props.onRegenerateResponse}
            className="message-action-btn"
            title="Regenerate response"
          >
            <RegenerateIcon />
          </button>
        )}
        {canContinue && (
          <button
            onClick={this.props.onContinueGeneration}
            className="message-action-btn"
            title="Continue generation"
          >
            <ContinueIcon />
          </button>
        )}
      </div>
    ) : null;

    const hasActions = Boolean(userAction || aiActions);

    return (
      <div key={message.id} className={messageClassNames} data-message-id={message.id}>
        <div className="message-bubble">
          <div className="message-body">
            {showRetrievedContextInline && message.retrievalData && (
              this.renderRetrievedContextBlock(message.id, message.retrievalData)
            )}

            {sender === 'ai' && thinkingContent && (
              <ThinkingBlock isStreaming={isStreaming}>
                {thinkingContent}
              </ThinkingBlock>
            )}

            <div className="message-content">
              {mainContent}
            </div>

            {isStreaming && displayContent.length === 0 && (
              <span className="typing-indicator" role="status" aria-live="polite">
                <span className="typing-dot"></span>
                <span className="typing-dot"></span>
                <span className="typing-dot"></span>
              </span>
            )}
          </div>

          <div className="message-meta">
            <span className="message-timestamp">{formatTimestamp(timestamp)}</span>
            {isEdited && (
              <span className="message-edited-indicator">Edited</span>
            )}
            {hasActions && (
              <div className="message-actions">
                {userAction}
                {aiActions}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  };

  /**
   * Render loading indicator
   */
  renderLoadingIndicator = () => {
    return (
      <div className="loading-indicator">
        <div className="loading-dots">
          <div className="loading-dot"></div>
          <div className="loading-dot"></div>
          <div className="loading-dot"></div>
        </div>
      </div>
    );
  };

  /**
   * Render error message
   */
  renderError = () => {
    if (!this.props.error) return null;
    
    return (
      <div className="error-message">
        {this.props.error}
      </div>
    );
  };

  /**
   * Render empty state when no messages
   */
  renderEmptyState = () => {
    if (this.props.messages.length > 0) return null;
    
    return (
      <div className="empty-state">
        <div className="empty-state-icon">
          💬
        </div>
        <div className="empty-state-text">
          Start a conversation by typing a message below.
        </div>
      </div>
    );
  };

  /**
   * Render scroll to bottom button
   */
  renderScrollToBottomButton = () => {
    const { showScrollToBottom, onScrollToBottom } = this.props;
    
    if (!showScrollToBottom || !onScrollToBottom) return null;
    
    return (
      <button
        type="button"
        onClick={onScrollToBottom}
        className="scroll-to-bottom-button"
        title="Scroll to bottom"
        aria-label="Scroll to bottom"
      >
        <ScrollToBottomIcon />
      </button>
    );
  };

  render() {
    const { messages, isLoadingHistory, chatHistoryRef } = this.props;

    return (
      <div className="chat-history-container">
        <div 
          ref={chatHistoryRef}
          className="chat-history"
          onScroll={this.props.onScroll}
          onPointerDown={this.handlePointerDown}
          onWheel={this.handleWheel}
          onTouchStart={this.handleTouchStart}
          onKeyDown={this.handleKeyDown}
          tabIndex={0}
        >
          {/* Show error if any */}
          {this.renderError()}
          
          {/* Show loading indicator for history */}
          {isLoadingHistory && this.renderLoadingIndicator()}
          
          {/* Show empty state or messages */}
          {!isLoadingHistory && messages.length === 0 ? (
            this.renderEmptyState()
          ) : (
            messages.map(message => this.renderMessage(message))
          )}
        </div>
        
        {/* Scroll to bottom button */}
        {this.renderScrollToBottomButton()}
      </div>
    );
  }
}

export default ChatHistory;
