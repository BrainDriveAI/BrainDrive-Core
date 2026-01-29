/**
 * Integration test for Login component with enhanced error handling
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import Login from '../../../pages/Login';

// Mock the AuthContext
const mockLogin = jest.fn();
const mockRegister = jest.fn();

jest.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({
    login: mockLogin,
    register: mockRegister,
    user: null,
    isAuthenticated: false,
    isLoading: false
  })
}));

// Mock the navigate function
const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate
}));

const theme = createTheme();

const renderLogin = () => {
  return render(
    <BrowserRouter>
      <ThemeProvider theme={theme}>
        <Login />
      </ThemeProvider>
    </BrowserRouter>
  );
};

describe('Login Component Error Handling', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('should display enhanced error message for 401 unauthorized', async () => {
    // Mock login to reject with 401 error
    const mockError = {
      response: {
        status: 401,
        data: {
          detail: 'Invalid email or password'
        }
      }
    };
    mockLogin.mockRejectedValueOnce(mockError);

    renderLogin();

    // Fill in login form
    const emailInput = screen.getAllByLabelText(/email/i, { selector: 'input' })[0];
    const passwordInput = screen.getAllByLabelText(/^password/i, { selector: 'input' })[0];
    const loginButton = screen.getByRole('button', { name: /sign in/i });

    fireEvent.change(emailInput, { target: { value: 'test@example.com' } });
    fireEvent.change(passwordInput, { target: { value: 'wrongpassword' } });
    fireEvent.click(loginButton);

    // Wait for error message to appear
    await waitFor(() => {
      expect(screen.getByText(/incorrect password/i)).toBeInTheDocument();
    });

    // Check that suggestions are available
    expect(screen.getByRole('button', { name: /need help\?/i })).toBeInTheDocument();
  });

  it('should display enhanced error message for account not found', async () => {
    // Mock login to reject with 404 error
    const mockError = {
      response: {
        status: 404,
        data: {
          detail: 'Account not found'
        }
      }
    };
    mockLogin.mockRejectedValueOnce(mockError);

    renderLogin();

    // Fill in login form
    const emailInput = screen.getAllByLabelText(/email/i, { selector: 'input' })[0];
    const passwordInput = screen.getAllByLabelText(/^password/i, { selector: 'input' })[0];
    const loginButton = screen.getByRole('button', { name: /sign in/i });

    fireEvent.change(emailInput, { target: { value: 'nonexistent@example.com' } });
    fireEvent.change(passwordInput, { target: { value: 'password123' } });
    fireEvent.click(loginButton);

    // Wait for error message to appear
    await waitFor(() => {
      expect(screen.getByText(/couldn't find an account with this email address/i)).toBeInTheDocument();
    });

    // Check that "Create Account" action is available
    expect(screen.getByText(/create account/i)).toBeInTheDocument();
  });

  it('should display enhanced error message for network errors', async () => {
    // Mock login to reject with network error
    const mockError = {
      message: 'Network Error'
    };
    mockLogin.mockRejectedValueOnce(mockError);

    renderLogin();

    // Fill in login form
    const emailInput = screen.getAllByLabelText(/email/i, { selector: 'input' })[0];
    const passwordInput = screen.getAllByLabelText(/^password/i, { selector: 'input' })[0];
    const loginButton = screen.getByRole('button', { name: /sign in/i });

    fireEvent.change(emailInput, { target: { value: 'test@example.com' } });
    fireEvent.change(passwordInput, { target: { value: 'password123' } });
    fireEvent.click(loginButton);

    // Wait for error message to appear
    await waitFor(() => {
      expect(screen.getAllByText(/unable to connect to the server/i).length).toBeGreaterThan(0);
    });

    // Check that suggestions are available
    expect(screen.getByRole('button', { name: /need help\?/i })).toBeInTheDocument();
  });

  it('should show real-time email validation', async () => {
    renderLogin();

    // Switch to registration tab
    const registerTab = screen.getByRole('tab', { name: /register/i });
    fireEvent.click(registerTab);

    // Find email input in registration form
    const emailInputs = screen.getAllByLabelText(/email/i, { selector: 'input' });
    const registrationEmailInput = emailInputs[emailInputs.length - 1]; // Get the last one (registration form)

    // Type invalid email
    fireEvent.change(registrationEmailInput, { target: { value: 'invalid-email' } });
    fireEvent.blur(registrationEmailInput);

    // Wait for validation message
    await waitFor(() => {
      expect(screen.getByText(/please enter a valid email address/i)).toBeInTheDocument();
    });
  });

  it('should show password strength indicator', async () => {
    renderLogin();

    // Switch to registration tab
    const registerTab = screen.getByRole('tab', { name: /register/i });
    fireEvent.click(registerTab);

    // Find password input in registration form
    const registrationPasswordInput = screen.getByLabelText(/^password/i);

    // Type a weak password
    fireEvent.change(registrationPasswordInput, { target: { value: 'weak' } });

    // Wait for password strength indicator
    await waitFor(() => {
      expect(screen.getByText(/password strength/i)).toBeInTheDocument();
    });
  });

  it('should persist error messages and not flicker', async () => {
    // Mock login to reject with 401 error
    const mockError = {
      response: {
        status: 401,
        data: {
          detail: 'Invalid email or password'
        }
      }
    };
    mockLogin.mockRejectedValueOnce(mockError);

    renderLogin();

    // Fill in login form
    const emailInput = screen.getAllByLabelText(/email/i, { selector: 'input' })[0];
    const passwordInput = screen.getAllByLabelText(/^password/i, { selector: 'input' })[0];
    const loginButton = screen.getByRole('button', { name: /sign in/i });

    fireEvent.change(emailInput, { target: { value: 'test@example.com' } });
    fireEvent.change(passwordInput, { target: { value: 'wrongpassword' } });
    fireEvent.click(loginButton);

    // Wait for error message to appear
    await waitFor(() => {
      expect(screen.getByText(/incorrect password/i)).toBeInTheDocument();
    });

    // Wait a bit more to ensure the message persists
    await new Promise(resolve => setTimeout(resolve, 600));

    // Error message should still be visible
    expect(screen.getByText(/incorrect password/i)).toBeInTheDocument();
  });
});
