/**
 * Test suite for authentication error handler utilities
 */

import {
  getLoginErrorMessage,
  getRegistrationErrorMessage,
  validateEmail,
  validatePassword,
  validateUsername,
  validatePasswordConfirmation,
  AuthError,
  ErrorResponse
} from '../authErrorHandler';

describe('Authentication Error Handler', () => {
  describe('getLoginErrorMessage', () => {
    it('should handle network errors', () => {
      const error: ErrorResponse = {
        message: 'Network Error'
      };
      
      const result = getLoginErrorMessage(error);
      
      expect(result.code).toBe('NETWORK_ERROR');
      expect(result.message).toContain('Unable to connect to the server');
      expect(result.suggestions).toContain('Check your internet connection');
      expect(result.actionable).toBe(true);
    });

    it('should handle 401 unauthorized errors', () => {
      const error: ErrorResponse = {
        response: {
          status: 401,
          data: {
            detail: 'Invalid email or password'
          }
        }
      };
      
      const result = getLoginErrorMessage(error);
      
      expect(result.code).toBe('INVALID_PASSWORD');
      expect(result.message).toContain('Incorrect password');
      expect(result.suggestions).toContain('Make sure you\'re typing the correct password');
      expect(result.actionable).toBe(true);
    });

    it('should handle 404 account not found errors', () => {
      const error: ErrorResponse = {
        response: {
          status: 404,
          data: {
            detail: 'Account not found'
          }
        }
      };
      
      const result = getLoginErrorMessage(error);
      
      expect(result.code).toBe('ACCOUNT_NOT_FOUND');
      expect(result.message).toContain('We couldn\'t find an account with this email address');
      expect(result.field).toBe('email');
      expect(result.suggestions).toContain('Double-check that you typed your email correctly');
    });

    it('should handle server errors', () => {
      const error: ErrorResponse = {
        response: {
          status: 500,
          data: {}
        }
      };
      
      const result = getLoginErrorMessage(error);
      
      expect(result.code).toBe('SERVER_ERROR');
      expect(result.message).toContain('Something went wrong on our end');
      expect(result.actionable).toBe(false);
    });
  });

  describe('getRegistrationErrorMessage', () => {
    it('should handle email already registered errors', () => {
      const error: ErrorResponse = {
        response: {
          status: 400,
          data: {
            detail: 'Email already registered'
          }
        }
      };
      
      const result = getRegistrationErrorMessage(error);
      
      expect(result.code).toBe('EMAIL_EXISTS');
      expect(result.message).toContain('An account with this email already exists');
      expect(result.field).toBe('email');
      expect(result.suggestions).toContain('Try logging in with this email instead');
    });

    it('should handle username already taken errors', () => {
      const error: ErrorResponse = {
        response: {
          status: 400,
          data: {
            detail: 'Username already taken'
          }
        }
      };
      
      const result = getRegistrationErrorMessage(error);
      
      expect(result.code).toBe('USERNAME_EXISTS');
      expect(result.message).toContain('already using this username');
      expect(result.field).toBe('username');
      expect(result.suggestions).toContain('Try adding numbers to your username (like \"john123\")');
    });

    it('should handle password validation errors', () => {
      const error: ErrorResponse = {
        response: {
          status: 400,
          data: {
            detail: 'Password does not meet requirements'
          }
        }
      };
      
      const result = getRegistrationErrorMessage(error);
      
      expect(result.code).toBe('INVALID_PASSWORD');
      expect(result.field).toBe('password');
      expect(result.suggestions).toContain('Use at least 8 characters');
    });
  });

  describe('validateEmail', () => {
    it('should validate correct email addresses', () => {
      const validEmails = [
        'user@example.com',
        'test.email@domain.co.uk',
        'user+tag@example.org'
      ];

      validEmails.forEach(email => {
        const result = validateEmail(email);
        expect(result.isValid).toBe(true);
        expect(result.message).toBeUndefined();
      });
    });

    it('should reject invalid email addresses', () => {
      const invalidEmails = [
        'invalid-email',
        '@domain.com',
        'user@',
        'user@domain',
        ''
      ];

      invalidEmails.forEach(email => {
        const result = validateEmail(email);
        expect(result.isValid).toBe(false);
        expect(result.message).toBeDefined();
      });
    });

    it('should handle empty email', () => {
      const result = validateEmail('');
      expect(result.isValid).toBe(false);
      expect(result.message).toBe('Email is required');
    });
  });

  describe('validatePassword', () => {
    it('should validate strong passwords', () => {
      const strongPasswords = [
        'MySecure123!',
        'Complex@Pass1',
        'Strong#Password2024'
      ];

      strongPasswords.forEach(password => {
        const result = validatePassword(password);
        expect(result.isValid).toBe(true);
        expect(result.strength).toBe('strong');
        expect(result.suggestions).toHaveLength(0);
      });
    });

    it('should identify weak passwords', () => {
      const weakPasswords = [
        'weak',
        '1234567',
        'short',
        'abcdef'
      ];

      weakPasswords.forEach(password => {
        const result = validatePassword(password);
        expect(result.isValid).toBe(false);
        expect(result.strength).toBe('weak');
        expect(result.suggestions.length).toBeGreaterThan(0);
      });
    });

    it('should provide specific suggestions for password improvement', () => {
      const result = validatePassword('password');
      
      expect(result.suggestions).toContain('Include uppercase letters');
      expect(result.suggestions).toContain('Include numbers');
      expect(result.suggestions).toContain('Include special characters');
    });

    it('should handle empty password', () => {
      const result = validatePassword('');
      
      expect(result.isValid).toBe(false);
      expect(result.message).toBe('Password is required');
      expect(result.strength).toBe('weak');
    });

    it('should classify medium strength passwords', () => {
      const mediumPasswords = [
        'Password',  // Missing number
        'password1', // Missing uppercase
        'PASSWORD1', // Missing lowercase
        '12345678'   // Missing letters
      ];

      mediumPasswords.forEach(password => {
        const result = validatePassword(password);
        expect(result.strength).toBe('medium');
      });
    });
  });

  describe('validateUsername', () => {
    it('should validate correct usernames', () => {
      const validUsernames = [
        'user123',
        'test_user',
        'user-name',
        'validuser'
      ];

      validUsernames.forEach(username => {
        const result = validateUsername(username);
        expect(result.isValid).toBe(true);
        expect(result.message).toBeUndefined();
      });
    });

    it('should reject invalid usernames', () => {
      const invalidUsernames = [
        'ab',           // Too short
        'user@name',    // Invalid character
        'user name',    // Space not allowed
        'a'.repeat(51)  // Too long
      ];

      invalidUsernames.forEach(username => {
        const result = validateUsername(username);
        expect(result.isValid).toBe(false);
        expect(result.message).toBeDefined();
      });
    });

    it('should handle empty username', () => {
      const result = validateUsername('');
      expect(result.isValid).toBe(false);
      expect(result.message).toBe('Username is required');
    });
  });

  describe('validatePasswordConfirmation', () => {
    it('should validate matching passwords', () => {
      const password = 'MySecure123!';
      const confirmPassword = 'MySecure123!';
      
      const result = validatePasswordConfirmation(password, confirmPassword);
      
      expect(result.isValid).toBe(true);
      expect(result.message).toBeUndefined();
    });

    it('should reject non-matching passwords', () => {
      const password = 'MySecure123!';
      const confirmPassword = 'DifferentPassword!';
      
      const result = validatePasswordConfirmation(password, confirmPassword);
      
      expect(result.isValid).toBe(false);
      expect(result.message).toBe('Passwords do not match');
    });

    it('should handle empty confirmation password', () => {
      const password = 'MySecure123!';
      const confirmPassword = '';
      
      const result = validatePasswordConfirmation(password, confirmPassword);
      
      expect(result.isValid).toBe(false);
      expect(result.message).toBe('Please confirm your password');
    });
  });
});
