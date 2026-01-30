import React from 'react';

export interface DropdownOption {
  value: string;
  label: string;
  description?: string;
  keywords?: string[];
  disabled?: boolean;
}

interface SearchableDropdownProps {
  id: string;
  value: string;
  options: DropdownOption[];
  onSelect: (value: string) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  noResultsText?: string;
  disabled?: boolean;
  loading?: boolean;
  className?: string;
  triggerClassName?: string;
  menuClassName?: string;
  inputClassName?: string;
  renderOption?: (
    option: DropdownOption,
    state: { isActive: boolean; isSelected: boolean }
  ) => React.ReactNode;
  renderValue?: (selected: DropdownOption | null) => React.ReactNode;
}

interface SearchableDropdownState {
  isOpen: boolean;
  query: string;
  activeIndex: number;
}

class SearchableDropdown extends React.Component<
  SearchableDropdownProps,
  SearchableDropdownState
> {
  static defaultProps = {
    placeholder: 'Select...',
    searchPlaceholder: 'Type to search...',
    noResultsText: 'No results found',
    disabled: false,
    loading: false,
    className: '',
    triggerClassName: '',
    menuClassName: '',
    inputClassName: ''
  };

  private buttonRef = React.createRef<HTMLButtonElement>();
  private inputRef = React.createRef<HTMLInputElement>();
  private listRef = React.createRef<HTMLDivElement>();

  constructor(props: SearchableDropdownProps) {
    super(props);
    this.state = {
      isOpen: false,
      query: '',
      activeIndex: -1
    };
  }

  componentDidMount(): void {
    document.addEventListener('mousedown', this.handleDocumentClick);
  }

  componentWillUnmount(): void {
    document.removeEventListener('mousedown', this.handleDocumentClick);
  }

  componentDidUpdate(
    prevProps: SearchableDropdownProps,
    prevState: SearchableDropdownState
  ): void {
    if (!prevState.isOpen && this.state.isOpen) {
      this.focusInput();
    }

    if (
      prevState.isOpen !== this.state.isOpen ||
      prevState.query !== this.state.query ||
      prevProps.options !== this.props.options ||
      prevProps.value !== this.props.value
    ) {
      this.syncActiveIndex();
    }
  }

  private focusInput() {
    const input = this.inputRef.current;
    if (input) {
      input.focus({ preventScroll: true });
      input.select();
    }
  }

  private handleDocumentClick = (event: MouseEvent) => {
    if (!this.state.isOpen) {
      return;
    }

    const target = event.target as Node;
    const button = this.buttonRef.current;
    const list = this.listRef.current;

    if (button && button.contains(target)) {
      return;
    }

    if (list && list.contains(target)) {
      return;
    }

    this.closeMenu();
  };

  private getFilteredOptions(): DropdownOption[] {
    const { options } = this.props;
    const normalizedQuery = this.state.query.trim().toLowerCase();

    if (!normalizedQuery) {
      return options;
    }

    return options.filter(option => {
      const haystack = [
        option.label,
        option.description,
        ...(option.keywords || [])
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();

      return haystack.includes(normalizedQuery);
    });
  }

  private syncActiveIndex() {
    if (!this.state.isOpen) {
      if (this.state.activeIndex !== -1) {
        this.setState({ activeIndex: -1 });
      }
      return;
    }

    const filteredOptions = this.getFilteredOptions();

    if (filteredOptions.length === 0) {
      if (this.state.activeIndex !== -1) {
        this.setState({ activeIndex: -1 });
      }
      return;
    }

    const selectedIndex = filteredOptions.findIndex(
      option => option.value === this.props.value
    );

    let nextIndex = selectedIndex >= 0 ? selectedIndex : this.state.activeIndex;
    if (nextIndex < 0 || nextIndex >= filteredOptions.length) {
      nextIndex = 0;
    }

    if (nextIndex !== this.state.activeIndex) {
      this.setState({ activeIndex: nextIndex });
    }
  }

  private openMenu = () => {
    if (this.props.disabled || this.props.loading) {
      return;
    }

    if (!this.state.isOpen) {
      this.setState({ isOpen: true });
    }
  };

  private closeMenu = () => {
    if (!this.state.isOpen && this.state.query === '') {
      return;
    }

    this.setState({ isOpen: false, query: '', activeIndex: -1 }, () => {
      const button = this.buttonRef.current;
      if (button) {
        button.focus({ preventScroll: true });
      }
    });
  };

  private toggleMenu = () => {
    if (this.state.isOpen) {
      this.closeMenu();
    } else {
      this.openMenu();
    }
  };

  private handleSelect = (option: DropdownOption) => {
    if (option.disabled) {
      return;
    }

    this.props.onSelect(option.value);
    this.closeMenu();
  };

  private handleButtonKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>
  ) => {
    if (this.props.disabled || this.props.loading) {
      return;
    }

    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!this.state.isOpen) {
        this.openMenu();
      }
      return;
    }

    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      this.toggleMenu();
      return;
    }

    if (
      event.key.length === 1 &&
      !event.altKey &&
      !event.ctrlKey &&
      !event.metaKey
    ) {
      event.preventDefault();
      this.setState(prevState => ({
        isOpen: true,
        query: `${prevState.isOpen ? prevState.query : ''}${event.key}`
      }));
    }
  };

  private handleInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    this.setState({ query: event.target.value });
  };

  private handleInputKeyDown = (
    event: React.KeyboardEvent<HTMLInputElement>
  ) => {
    if (!this.state.isOpen) {
      return;
    }

    const filteredOptions = this.getFilteredOptions();
    const optionCount = filteredOptions.length;

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (optionCount === 0) {
        return;
      }

      this.setState(prevState => {
        const next = prevState.activeIndex + 1;
        const activeIndex = next >= optionCount ? 0 : next;
        return { activeIndex };
      });
      return;
    }

    if (event.key === 'ArrowUp') {
      event.preventDefault();
      if (optionCount === 0) {
        return;
      }

      this.setState(prevState => {
        const next = prevState.activeIndex - 1;
        const activeIndex = next < 0 ? optionCount - 1 : next;
        return { activeIndex };
      });
      return;
    }

    if (event.key === 'Enter') {
      event.preventDefault();
      const { activeIndex } = this.state;
      if (activeIndex >= 0 && activeIndex < optionCount) {
        this.handleSelect(filteredOptions[activeIndex]);
      }
      return;
    }

    if (event.key === 'Escape') {
      event.preventDefault();
      this.closeMenu();
    }
  };

  private handleOptionMouseEnter = (index: number) => {
    if (index !== this.state.activeIndex) {
      this.setState({ activeIndex: index });
    }
  };

  private handleOptionMouseDown = (
    event: React.MouseEvent<HTMLDivElement>,
    option: DropdownOption
  ) => {
    event.preventDefault();
    this.handleSelect(option);
  };

  render() {
    const {
      id,
      value,
      placeholder,
      searchPlaceholder,
      noResultsText,
      disabled,
      loading,
      className,
      triggerClassName,
      menuClassName,
      inputClassName,
      renderOption,
      renderValue
    } = this.props;

    const { isOpen, activeIndex, query } = this.state;
    const filteredOptions = this.getFilteredOptions();
    const selectedOption = this.props.options.find(
      option => option.value === value
    ) || null;
    const listboxId = `${id}-listbox`;
    const activeOptionId = isOpen && activeIndex >= 0
      ? `${id}-option-${activeIndex}`
      : undefined;

    return (
      <div
        className={`searchable-dropdown ${className} ${isOpen ? 'open' : ''} ${
          disabled || loading ? 'disabled' : ''
        }`.trim()}
        role="combobox"
        aria-haspopup="listbox"
        aria-owns={listboxId}
        aria-expanded={isOpen}
        aria-controls={isOpen ? listboxId : undefined}
      >
        <button
          ref={this.buttonRef}
          type="button"
          className={`searchable-dropdown-trigger ${triggerClassName}`.trim()}
          aria-haspopup="listbox"
          aria-expanded={isOpen}
          aria-controls={isOpen ? listboxId : undefined}
          aria-disabled={disabled || loading}
          onClick={this.toggleMenu}
          onKeyDown={this.handleButtonKeyDown}
          disabled={disabled || loading}
        >
          <span className="searchable-dropdown-value">
            {loading
              ? 'Loading...'
              : renderValue
              ? renderValue(selectedOption)
              : selectedOption?.label || placeholder}
          </span>
          <span className="searchable-dropdown-icon" aria-hidden="true">▾</span>
        </button>

        {isOpen && (
          <div
            ref={this.listRef}
            className={`searchable-dropdown-menu ${menuClassName}`.trim()}
          >
            <div className="searchable-dropdown-search">
              <input
                ref={this.inputRef}
                type="text"
                className={`searchable-dropdown-input ${inputClassName}`.trim()}
                placeholder={searchPlaceholder}
                value={query}
                onChange={this.handleInputChange}
                onKeyDown={this.handleInputKeyDown}
                aria-controls={listboxId}
                aria-activedescendant={activeOptionId}
              />
            </div>

            <div className="searchable-dropdown-options" role="listbox" id={listboxId}>
              {filteredOptions.length === 0 ? (
                <div className="searchable-dropdown-empty" role="alert">
                  {noResultsText}
                </div>
              ) : (
                filteredOptions.map((option, index) => {
                  const optionId = `${id}-option-${index}`;
                  const isSelected = option.value === value;
                  const isActive = index === activeIndex;

                  return (
                    <div
                      key={optionId}
                      id={optionId}
                      role="option"
                      aria-selected={isSelected}
                      aria-disabled={option.disabled}
                      className={`searchable-dropdown-option ${
                        isSelected ? 'selected' : ''
                      } ${isActive ? 'active' : ''} ${
                        option.disabled ? 'disabled' : ''
                      }`.trim()}
                      onMouseDown={event =>
                        this.handleOptionMouseDown(event, option)
                      }
                      onMouseEnter={() => this.handleOptionMouseEnter(index)}
                    >
                      {renderOption
                        ? renderOption(option, {
                            isActive,
                            isSelected
                          })
                        : (
                            <>
                              <span className="searchable-dropdown-option-label">
                                {option.label}
                              </span>
                              {option.description && (
                                <span className="searchable-dropdown-option-description">
                                  {option.description}
                                </span>
                              )}
                            </>
                          )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}
      </div>
    );
  }
}

export default SearchableDropdown;
