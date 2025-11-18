import React, { useState, useCallback, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Container, Typography, Box, Paper, Alert, AlertTitle, Button, TextField, InputAdornment } from '@mui/material';
import { Add as AddIcon, Search as SearchIcon } from '@mui/icons-material';
import PersonaGrid from '../features/personas/components/PersonaGrid';
import DeleteConfirmationDialog from '../features/personas/components/DeleteConfirmationDialog';
import usePersonas from '../features/personas/hooks/usePersonas';
import { Persona } from '../features/personas/types';

/**
 * The main page for browsing and managing personas
 */
const PersonasPage: React.FC = () => {
  console.log('PersonasPage rendering');
  const renderCount = useRef(0);
  
  useEffect(() => {
    renderCount.current += 1;
    console.log(`PersonasPage rendered ${renderCount.current} times`);
  });
  
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 16; // 4x4 grid
  
  // Delete confirmation dialog state
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [personaToDelete, setPersonaToDelete] = useState<Persona | null>(null);
  const [deleting, setDeleting] = useState(false);
  
  const {
    personas,
    totalPersonas,
    loading,
    error,
    togglePersonaStatus,
    deletePersona
  } = usePersonas({
    search: searchQuery,
    page,
    pageSize
  });

  const handleSearch = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    const query = event.target.value;
    console.log(`Search query changed to: ${query}`);
    setSearchQuery(query);
    setPage(1); // Reset to first page on new search
  }, []);

  const handlePersonaClick = useCallback((persona: Persona) => {
    console.log(`Persona clicked: ${persona.name} (edit)`);
    navigate(`/personas/${persona.id}/edit`);
  }, [navigate]);

  const handleToggleStatus = useCallback(async (persona: Persona, enabled: boolean) => {
    console.log(`Toggle status for persona ${persona.name} to ${enabled}`);
    await togglePersonaStatus(persona.id, enabled);
  }, [togglePersonaStatus]);

  const handlePageChange = useCallback((newPage: number) => {
    console.log(`Page changed to: ${newPage}`);
    setPage(newPage);
  }, []);

  const handleCreatePersona = useCallback(() => {
    navigate('/personas/new');
  }, [navigate]);

  const handleDeletePersona = useCallback((persona: Persona) => {
    setPersonaToDelete(persona);
    setDeleteDialogOpen(true);
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    if (!personaToDelete) return;
    
    try {
      setDeleting(true);
      await deletePersona(personaToDelete.id);
      setDeleteDialogOpen(false);
      setPersonaToDelete(null);
    } catch (err) {
      console.error('Error deleting persona:', err);
      // Error handling is done in the hook
    } finally {
      setDeleting(false);
    }
  }, [personaToDelete, deletePersona]);

  const handleCancelDelete = useCallback(() => {
    setDeleteDialogOpen(false);
    setPersonaToDelete(null);
  }, []);

  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Typography variant="h4" component="h1">
          Personas
        </Typography>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={handleCreatePersona}
          sx={{ minWidth: 160 }}
        >
          Create Persona
        </Button>
      </Box>
      
      <Paper sx={{ p: 3, mb: 3 }}>
        <TextField
          fullWidth
          placeholder="Search personas..."
          value={searchQuery}
          onChange={handleSearch}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon />
              </InputAdornment>
            ),
          }}
          sx={{ mb: 2 }}
        />
        
        <Typography variant="body2" color="text.secondary">
          {totalPersonas > 0 
            ? `Found ${totalPersonas} persona${totalPersonas === 1 ? '' : 's'}`
            : 'No personas found'
          }
        </Typography>
      </Paper>
      
      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          <AlertTitle>Error</AlertTitle>
          {error.message}
        </Alert>
      )}
      
      <Box sx={{ mb: 4 }}>
        <PersonaGrid
          personas={personas}
          onPersonaClick={handlePersonaClick}
          onToggleStatus={handleToggleStatus}
          onDelete={handleDeletePersona}
          loading={loading}
          pagination={{
            page,
            pageSize,
            totalItems: totalPersonas,
            onPageChange: handlePageChange
          }}
        />
      </Box>
      
      {/* Delete Confirmation Dialog */}
      <DeleteConfirmationDialog
        open={deleteDialogOpen}
        persona={personaToDelete}
        onConfirm={handleConfirmDelete}
        onCancel={handleCancelDelete}
        loading={deleting}
      />
    </Container>
  );
};

export default PersonasPage;
