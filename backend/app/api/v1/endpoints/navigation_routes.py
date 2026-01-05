from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.core.database import get_db
from app.core.auth_deps import require_user
from app.core.auth_context import AuthContext
from app.models.navigation import NavigationRoute
from app.models.user import User
from app.schemas.navigation import (
    NavigationRouteCreate,
    NavigationRouteResponse,
    NavigationRouteUpdate,
    NavigationRouteDetailResponse,
    NavigationRouteListResponse,
    NavigationRouteTree,
    NavigationRouteMove,
    NavigationRouteBatchUpdate
)
from app.services.navigation_service import get_user_navigation_route, ensure_route_belongs_to_user

router = APIRouter()

@router.get("/tree", response_model=List[NavigationRouteTree])
async def get_navigation_tree(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get navigation routes as a tree structure"""
    try:
        # Get all navigation routes for the user (flat list)
        result = await db.execute(
            select(NavigationRoute)
            .where(NavigationRoute.creator_id == auth.user_id)
            .order_by(NavigationRoute.display_order.asc())
        )
        all_routes = result.scalars().all()
        
        # Build tree structure manually without accessing SQLAlchemy relationships
        def build_tree_response(parent_id: str = None, depth: int = 0) -> List[NavigationRouteTree]:
            result = []
            # Find all routes with the given parent_id
            children = [route for route in all_routes if route.parent_id == parent_id]
            # Sort by display_order
            children.sort(key=lambda x: x.display_order or 0)
            
            for route in children:
                route_dict = {
                    "id": route.id,
                    "name": route.name,
                    "route": route.route,
                    "icon": route.icon,
                    "description": route.description,
                    "order": route.order,
                    "is_visible": route.is_visible,
                    "creator_id": route.creator_id,
                    "is_system_route": route.is_system_route,
                    "default_component_id": route.default_component_id,
                    "default_page_id": route.default_page_id,
                    "can_change_default": route.can_change_default,
                    "parent_id": route.parent_id,
                    "display_order": route.display_order,
                    "is_collapsible": route.is_collapsible,
                    "is_expanded": route.is_expanded,
                    "created_at": route.created_at.isoformat() if route.created_at else None,
                    "updated_at": route.updated_at.isoformat() if route.updated_at else None,
                    "depth_level": depth,
                    "children": build_tree_response(route.id, depth + 1)
                }
                result.append(NavigationRouteTree(**route_dict))
            return result
        
        # Start with root nodes (parent_id is None)
        return build_tree_response(None, 0)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get navigation tree: {str(e)}"
        )

@router.post("/batch-update", response_model=List[NavigationRouteResponse])
async def batch_update_navigation_routes(
    updates: List[NavigationRouteBatchUpdate],
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Update multiple navigation routes in a single operation"""
    try:
        updated_routes = []
        
        for update in updates:
            route = await NavigationRoute.get_by_id(db, update.id)
            if not route:
                continue  # Skip non-existent routes
            
            # Check permissions - skip routes user doesn't own
            try:
                ensure_route_belongs_to_user(route, auth)
            except HTTPException:
                continue  # Skip routes user doesn't own
            
            # Apply updates
            if update.parent_id is not None:
                route.parent_id = update.parent_id
            if update.display_order is not None:
                route.display_order = update.display_order
            if update.is_expanded is not None:
                route.is_expanded = update.is_expanded
            
            await route.save(db)
            updated_routes.append(NavigationRouteResponse(
                id=route.id,
                name=route.name,
                route=route.route,
                icon=route.icon,
                description=route.description,
                order=route.order,
                is_visible=route.is_visible,
                creator_id=route.creator_id,
                is_system_route=route.is_system_route,
                default_component_id=route.default_component_id,
                default_page_id=route.default_page_id,
                can_change_default=route.can_change_default,
                parent_id=route.parent_id,
                display_order=route.display_order,
                is_collapsible=route.is_collapsible,
                is_expanded=route.is_expanded,
                created_at=route.created_at.isoformat() if route.created_at else None,
                updated_at=route.updated_at.isoformat() if route.updated_at else None
            ))
        
        return updated_routes
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to batch update navigation routes: {str(e)}"
        )

@router.get("", response_model=List[NavigationRouteResponse])
async def get_navigation_routes(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user),
    visible_only: bool = Query(False, description="Filter to only visible routes")
):
    """
    Get all navigation routes.
    """
    try:
        routes_list = []
        
        try:
            if visible_only:
                # Filter visible routes by current user's ID
                routes = await NavigationRoute.get_visible_routes_by_user(db, auth.user_id)
                print(f"Found {len(routes)} visible routes for user {auth.user_id}")
            else:
                # Get routes created by the current user
                print(f"Fetching navigation routes for user {auth.user_id}")
                routes = await NavigationRoute.get_by_creator(db, auth.user_id)
                print(f"Found {len(routes)} total routes for user {auth.user_id}")
                
                # Log each route for debugging
                for route in routes:
                    try:
                        print(f"Route: {route.id}, {route.name}, {route.route}, creator: {route.creator_id}")
                    except Exception as e:
                        print(f"Error logging route: {e}")
            
            # Convert SQLAlchemy model instances to dictionaries with error handling
            for route in routes:
                try:
                    # Check if all required fields are present
                    if not hasattr(route, 'id') or not route.id:
                        print(f"Warning: Route missing id field")
                        continue
                    if not hasattr(route, 'name') or not route.name:
                        print(f"Warning: Route {route.id} missing name field")
                        continue
                    if not hasattr(route, 'route') or not route.route:
                        print(f"Warning: Route {route.id} missing route field")
                        continue
                    if not hasattr(route, 'creator_id') or not route.creator_id:
                        print(f"Warning: Route {route.id} missing creator_id field")
                        continue
                    
                    # Create dictionary with safe access to attributes
                    route_dict = {
                        "id": str(route.id),
                        "name": route.name,
                        "route": route.route,
                        "icon": route.icon if hasattr(route, 'icon') else None,
                        "description": route.description if hasattr(route, 'description') else None,
                        "order": route.order if hasattr(route, 'order') else 0,
                        "is_visible": route.is_visible if hasattr(route, 'is_visible') else True,
                        "creator_id": str(route.creator_id),
                        "created_at": route.created_at.isoformat() if hasattr(route, 'created_at') and route.created_at else None,
                        "updated_at": route.updated_at.isoformat() if hasattr(route, 'updated_at') and route.updated_at else None,
                        "is_system_route": route.is_system_route if hasattr(route, 'is_system_route') else False,
                        "default_component_id": route.default_component_id if hasattr(route, 'default_component_id') else None,
                        "default_page_id": str(route.default_page_id) if hasattr(route, 'default_page_id') and route.default_page_id else None,
                        "can_change_default": route.can_change_default if hasattr(route, 'can_change_default') else False,
                        # Hierarchical fields
                        "parent_id": route.parent_id if hasattr(route, 'parent_id') else None,
                        "display_order": route.display_order if hasattr(route, 'display_order') else 0,
                        "is_collapsible": route.is_collapsible if hasattr(route, 'is_collapsible') else True,
                        "is_expanded": route.is_expanded if hasattr(route, 'is_expanded') else True
                    }
                    routes_list.append(route_dict)
                except Exception as e:
                    print(f"Error processing route: {e}")
                    continue
        except Exception as inner_e:
            print(f"Inner exception in get_navigation_routes: {inner_e}")
            # Return an empty list instead of raising an exception
            return []
        
        print(f"Returning {len(routes_list)} routes")
        return routes_list
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch navigation routes: {str(e)}"
        )

@router.post("", response_model=NavigationRouteResponse, status_code=status.HTTP_201_CREATED)
async def create_navigation_route(
    route_data: NavigationRouteCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """
    Create a new navigation route.
    """
    try:
        # Check if route already exists
        existing_route = await NavigationRoute.get_by_route(db, route_data.route)
        if existing_route:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Route with path '{route_data.route}' already exists"
            )
        
        # Create new route
        new_route = NavigationRoute(
            name=route_data.name,
            route=route_data.route,
            icon=route_data.icon,
            description=route_data.description,
            order=route_data.order,
            is_visible=route_data.is_visible,
            creator_id=auth.user_id,
            can_change_default=route_data.can_change_default,
            default_component_id=route_data.default_component_id,
            default_page_id=str(route_data.default_page_id) if route_data.default_page_id is not None else None,
            # Hierarchical fields
            parent_id=route_data.parent_id,
            display_order=route_data.display_order,
            is_collapsible=route_data.is_collapsible,
            is_expanded=route_data.is_expanded
        )
        
        await new_route.save(db)
        
        # Convert SQLAlchemy model instance to dictionary
        return {
            "id": str(new_route.id),
            "name": new_route.name,
            "route": new_route.route,
            "icon": new_route.icon,
            "description": new_route.description,
            "order": new_route.order,
            "is_visible": new_route.is_visible,
            "creator_id": str(new_route.creator_id),
            "created_at": new_route.created_at.isoformat() if new_route.created_at else None,
            "updated_at": new_route.updated_at.isoformat() if new_route.updated_at else None,
            "is_system_route": new_route.is_system_route if hasattr(new_route, 'is_system_route') else False,
            "default_component_id": new_route.default_component_id if hasattr(new_route, 'default_component_id') else None,
            "default_page_id": str(new_route.default_page_id) if hasattr(new_route, 'default_page_id') and new_route.default_page_id else None,
            "can_change_default": new_route.can_change_default if hasattr(new_route, 'can_change_default') else False,
            # Hierarchical fields
            "parent_id": new_route.parent_id if hasattr(new_route, 'parent_id') else None,
            "display_order": new_route.display_order if hasattr(new_route, 'display_order') else 0,
            "is_collapsible": new_route.is_collapsible if hasattr(new_route, 'is_collapsible') else True,
            "is_expanded": new_route.is_expanded if hasattr(new_route, 'is_expanded') else True
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create navigation route: {str(e)}"
        )

@router.put("/{route_id}/move", response_model=NavigationRouteResponse)
async def move_navigation_route(
    route_id: str,
    move_data: NavigationRouteMove,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Move a navigation route to a different parent or position"""
    try:
        # Get route and ensure it belongs to current user
        route = await get_user_navigation_route(db, route_id, auth)
        
        # Move the route
        await route.move_to_parent(db, move_data.parent_id, move_data.display_order)
        
        return NavigationRouteResponse(
            id=route.id,
            name=route.name,
            route=route.route,
            icon=route.icon,
            description=route.description,
            order=route.order,
            is_visible=route.is_visible,
            creator_id=route.creator_id,
            is_system_route=route.is_system_route,
            default_component_id=route.default_component_id,
            default_page_id=route.default_page_id,
            can_change_default=route.can_change_default,
            parent_id=route.parent_id,
            display_order=route.display_order,
            is_collapsible=route.is_collapsible,
            is_expanded=route.is_expanded,
            created_at=route.created_at.isoformat() if route.created_at else None,
            updated_at=route.updated_at.isoformat() if route.updated_at else None
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to move navigation route: {str(e)}"
        )

@router.get("/{route_id}", response_model=NavigationRouteDetailResponse)
async def get_navigation_route(
    route_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """
    Get a specific navigation route by ID.
    """
    try:
        route = await NavigationRoute.get_by_id(db, route_id)
        if not route:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Navigation route with ID {route_id} not found"
            )
        
        # Convert SQLAlchemy model instance to dictionary with creator info
        return {
            "id": str(route.id),
            "name": route.name,
            "route": route.route,
            "icon": route.icon,
            "description": route.description,
            "order": route.order,
            "is_visible": route.is_visible,
            "creator_id": str(route.creator_id),
            "created_at": route.created_at.isoformat() if route.created_at else None,
            "updated_at": route.updated_at.isoformat() if route.updated_at else None,
            "is_system_route": route.is_system_route if hasattr(route, 'is_system_route') else False,
            "default_component_id": route.default_component_id if hasattr(route, 'default_component_id') else None,
            "default_page_id": str(route.default_page_id) if hasattr(route, 'default_page_id') and route.default_page_id else None,
            "can_change_default": route.can_change_default if hasattr(route, 'can_change_default') else False,
            # Hierarchical fields
            "parent_id": route.parent_id if hasattr(route, 'parent_id') else None,
            "display_order": route.display_order if hasattr(route, 'display_order') else 0,
            "is_collapsible": route.is_collapsible if hasattr(route, 'is_collapsible') else True,
            "is_expanded": route.is_expanded if hasattr(route, 'is_expanded') else True,
            "creator": {
                "id": str(route.creator.id),
                "username": route.creator.username,
                "email": route.creator.email
            } if route.creator else None
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch navigation route: {str(e)}"
        )

@router.put("/{route_id}", response_model=NavigationRouteResponse)
async def update_navigation_route(
    route_id: UUID,
    route_data: NavigationRouteUpdate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """
    Update a navigation route.
    """
    try:
        # Log the incoming request data for debugging
        print(f"Updating navigation route with ID: {route_id}")
        print(f"Route data received: {route_data.dict()}")
        # Get existing route
        route = await NavigationRoute.get_by_id(db, route_id)
        print(f"Route found: {route is not None}")
        if route:
            print(f"Route details: id={route.id}, name={route.name}, is_system_route={route.is_system_route}")
        
        if not route:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Navigation route with ID {route_id} not found"
            )
        
        # Check if route path is being changed and if it already exists
        if route_data.route and route_data.route != route.route:
            existing_route = await NavigationRoute.get_by_route(db, route_data.route)
            if existing_route:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Route with path '{route_data.route}' already exists"
                )
        
        # Update fields
        if route_data.name is not None:
            print(f"Updating name from '{route.name}' to '{route_data.name}'")
            route.name = route_data.name
        if route_data.route is not None:
            print(f"Updating route from '{route.route}' to '{route_data.route}'")
            route.route = route_data.route
        if route_data.icon is not None:
            print(f"Updating icon from '{route.icon}' to '{route_data.icon}'")
            route.icon = route_data.icon
        if route_data.description is not None:
            print(f"Updating description from '{route.description}' to '{route_data.description}'")
            route.description = route_data.description
        if route_data.order is not None:
            print(f"Updating order from '{route.order}' to '{route_data.order}'")
            route.order = route_data.order
        if route_data.is_visible is not None:
            print(f"Updating is_visible from '{route.is_visible}' to '{route_data.is_visible}'")
            route.is_visible = route_data.is_visible
        if route_data.default_component_id is not None:
            # Check if the route allows changing default component
            print(f"Updating default_component_id from '{route.default_component_id}' to '{route_data.default_component_id}'")
            print(f"Can change default: {route.can_change_default}")
            if not route.can_change_default and route.default_component_id is not None and route_data.default_component_id != route.default_component_id:
                print("Cannot change default component for this route")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot change default component for this route"
                )
            route.default_component_id = route_data.default_component_id
        # Handle default_page_id explicitly to allow setting it to null
        print(f"Handling default_page_id: {route_data.default_page_id}")
        print(f"Type: {type(route_data.default_page_id)}")
        
        # Check if default_page_id is in the request data
        if 'default_page_id' in route_data.__dict__:
            # Check if the route allows changing default page
            print(f"Updating default_page_id from '{route.default_page_id}' to '{route_data.default_page_id}'")
            print(f"Can change default: {route.can_change_default}")
            if not route.can_change_default and route.default_page_id is not None:
                print("Cannot change default page for this route")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot change default page for this route"
                )
            
            # Handle null value explicitly
            if route_data.default_page_id is None:
                print("Setting default_page_id to NULL")
                route.default_page_id = None
            else:
                # Convert UUID to string before saving to database
                print(f"Setting default_page_id to {route_data.default_page_id}")
                route.default_page_id = str(route_data.default_page_id)
        if route_data.can_change_default is not None:
            print(f"Updating can_change_default from '{route.can_change_default}' to '{route_data.can_change_default}'")
            route.can_change_default = route_data.can_change_default
        
        # Update hierarchical fields
        if route_data.parent_id is not None:
            print(f"Updating parent_id from '{route.parent_id}' to '{route_data.parent_id}'")
            route.parent_id = route_data.parent_id
        if route_data.display_order is not None:
            print(f"Updating display_order from '{route.display_order}' to '{route_data.display_order}'")
            route.display_order = route_data.display_order
        if route_data.is_collapsible is not None:
            print(f"Updating is_collapsible from '{route.is_collapsible}' to '{route_data.is_collapsible}'")
            route.is_collapsible = route_data.is_collapsible
        if route_data.is_expanded is not None:
            print(f"Updating is_expanded from '{route.is_expanded}' to '{route_data.is_expanded}'")
            route.is_expanded = route_data.is_expanded
        
        # Special handling for system routes
        if route.is_system_route:
            print("This is a system route - applying special validation")
            # For system routes, only allow updating icon, description, and order
            if route_data.name is not None and route_data.name != route.name:
                print("Cannot change name for system routes")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot change name for system routes"
                )
            if route_data.route is not None and route_data.route != route.route:
                print("Cannot change route path for system routes")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot change route path for system routes"
                )
        
        print("Saving route changes to database")
        await route.save(db)
        await route.save(db)
        
        # Convert SQLAlchemy model instance to dictionary
        return {
            "id": str(route.id),
            "name": route.name,
            "route": route.route,
            "icon": route.icon,
            "description": route.description,
            "order": route.order,
            "is_visible": route.is_visible,
            "creator_id": str(route.creator_id),
            "created_at": route.created_at.isoformat() if route.created_at else None,
            "updated_at": route.updated_at.isoformat() if route.updated_at else None,
            "is_system_route": route.is_system_route if hasattr(route, 'is_system_route') else False,
            "default_component_id": route.default_component_id if hasattr(route, 'default_component_id') else None,
            "default_page_id": str(route.default_page_id) if hasattr(route, 'default_page_id') and route.default_page_id else None,
            "can_change_default": route.can_change_default if hasattr(route, 'can_change_default') else False,
            # Hierarchical fields
            "parent_id": route.parent_id if hasattr(route, 'parent_id') else None,
            "display_order": route.display_order if hasattr(route, 'display_order') else 0,
            "is_collapsible": route.is_collapsible if hasattr(route, 'is_collapsible') else True,
            "is_expanded": route.is_expanded if hasattr(route, 'is_expanded') else True
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update navigation route: {str(e)}"
        )

@router.delete("/{route_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_navigation_route(
    route_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """
    Delete a navigation route.
    """
    try:
        # Convert route_id to string if it's a UUID
        route_id_str = str(route_id).replace('-', '')
        
        print(f"Attempting to delete route with ID: {route_id_str}")
        
        # Get existing route using raw SQL to avoid ORM issues
        query = text(f"SELECT * FROM navigation_routes WHERE id = '{route_id_str}'")
        result = await db.execute(query)
        route_data = result.fetchone()
        
        if not route_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Navigation route with ID {route_id} not found"
            )
        
        # Check if route is a system route
        is_system_route = route_data.is_system_route if hasattr(route_data, 'is_system_route') else False
        if is_system_route:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete system routes"
            )
        
        # Check if route has associated pages using raw SQL
        pages_query = text(f"SELECT COUNT(*) FROM pages WHERE navigation_route_id = '{route_id_str}'")
        pages_result = await db.execute(pages_query)
        page_count = pages_result.scalar()
        
        if page_count > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete route: {page_count} page(s) are using this route"
            )
        
        # Delete route using raw SQL
        delete_query = text(f"DELETE FROM navigation_routes WHERE id = '{route_id_str}'")
        await db.execute(delete_query)
        await db.commit()
        
        print(f"Successfully deleted route with ID: {route_id_str}")
        
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        print(f"Error deleting route: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete navigation route: {str(e)}"
        )


