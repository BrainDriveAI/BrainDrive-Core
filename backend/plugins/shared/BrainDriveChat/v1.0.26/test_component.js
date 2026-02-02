// Simple test to verify the component can be instantiated
const React = require('react');

// Mock the component export
const BrainDriveChat = require('./src/index.tsx').default;

// Test function component instantiation
function testComponentInstantiation() {
  try {
    // Create a mock props object
    const mockProps = {
      services: {
        settings: {
          get: (key) => null,
          set: (key, value) => Promise.resolve()
        },
        api: {
          get: () => Promise.resolve([]),
          post: () => Promise.resolve({})
        },
        theme: {
          getCurrentTheme: () => 'light',
          addThemeChangeListener: () => {},
          removeThemeChangeListener: () => {}
        },
        event: {
          sendMessage: () => {},
          subscribeToMessages: () => {},
          unsubscribeFromMessages: () => {}
        }
      }
    };

    // Try to create an instance
    const instance = new BrainDriveChat(mockProps);
    
    // Test if getSavedStreamingMode method exists and is callable
    if (typeof instance.getSavedStreamingMode === 'function') {
      console.log('✓ getSavedStreamingMode method exists and is callable');
      
      // Try to call it
      const result = instance.getSavedStreamingMode();
      console.log('✓ getSavedStreamingMode executed successfully, returned:', result);
      
      return true;
    } else {
      console.error('✗ getSavedStreamingMode method is not a function');
      console.error('Available methods:', Object.getOwnPropertyNames(instance).filter(name => typeof instance[name] === 'function'));
      return false;
    }
    
  } catch (error) {
    console.error('✗ Component instantiation failed:', error.message);
    return false;
  }
}

// Run the test
console.log('Testing BrainDriveChat component instantiation...');
const success = testComponentInstantiation();

if (success) {
  console.log('✓ All tests passed - component should load correctly');
} else {
  console.log('✗ Tests failed - component may have issues loading');
}