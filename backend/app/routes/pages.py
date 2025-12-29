from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from uuid import UUID

from app.core.database import get_db
from app.core.auth_deps import require_user, optional_user
from app.core.auth_context import AuthContext
from app.models.page import Page
from app.models.navigation import NavigationRoute
from app.models.user import User
from app.schemas.page import (
    PageCreate, 
    PageUpdate, 
    PageResponse, 
    PageDetailResponse, 
    PageListResponse,
    PageBackup,
    PagePublish,
    PageHierarchyUpdate
)

router = APIRouter(prefix="/pages", tags=["pages"])

@router.post("", response_model=PageResponse, status_code=status.HTTP_201_CREATED)
async def create_page(
    page_data: PageCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Create a new page."""
    # Check if route already exists
    existing_page = await Page.get_by_route(db, page_data.route)
    if existing_page:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A page with this route already exists"
        )
    
    # Check if navigation route exists if provided
    if page_data.navigation_route_id:
        nav_route = await NavigationRoute.get_by_id(db, page_data.navigation_route_id)
        if not nav_route:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The specified navigation route does not exist"
            )
        
        # Check if user has access to the navigation route
        if nav_route.creator_id != auth.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to use this navigation route"
            )
    
    # Create new page
    page = Page(
        name=page_data.name,
        route=page_data.route,
        parent_route=page_data.parent_route,
        content=page_data.content,
        creator_id=auth.user_id,
        navigation_route_id=page_data.navigation_route_id,
        description=page_data.description,
        icon=page_data.icon
    )
    
    await page.save(db)
    
    # Convert Page object to PageResponse
    return PageResponse(
        id=page.id,
        name=page.name,
        route=page.route,
        parent_route=page.parent_route,
        parent_type=page.parent_type,
        content=page.content,
        creator_id=page.creator_id,
        is_published=page.is_published,
        created_at=page.created_at,
        updated_at=page.updated_at,
        publish_date=page.publish_date,
        backup_date=page.backup_date,
        description=page.description,
        icon=page.icon,
        navigation_route_id=page.navigation_route_id
    )

@router.put("/{page_id}/hierarchy", response_model=PageResponse)
async def update_page_hierarchy(
    page_id: UUID,
    hierarchy_update: PageHierarchyUpdate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Update the hierarchy of a specific page."""
    page = await Page.get_by_id(db, page_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page not found"
        )
    
    # Check if user has permission to update the page
    # Convert both IDs to strings without hyphens for comparison
    creator_id_str = str(page.creator_id).replace('-', '')
    auth_id_str = str(auth.user_id).replace('-', '')
    
    if creator_id_str != auth_id_str:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to update this page"
        )
    
    # Validate that the route will be unique using the page name
    if hierarchy_update.parent_route is not None or hierarchy_update.parent_type is not None:
        try:
            is_valid, full_route = await Page.validate_route(
                db,
                page.name,  # Use the page name instead of route_segment
                hierarchy_update.parent_route if hierarchy_update.parent_route is not None else page.parent_route,
                hierarchy_update.parent_type if hierarchy_update.parent_type is not None else page.parent_type,
                page_id
            )
            
            if not is_valid:
                # Route already exists, generate a unique one by adding a timestamp
                import time
                import re
                
                # Clean the page name for use in the route
                clean_name = page.name.lower().replace(' ', '-')
                clean_name = re.sub(r'[^a-z0-9-]', '', clean_name)
                
                # Add timestamp to ensure uniqueness
                timestamp = int(time.time())
                unique_name = f"{clean_name}-{timestamp}"
                
                # Try again with the unique name
                is_valid, full_route = await Page.validate_route(
                    db,
                    unique_name,  # Use the unique name instead of page.name
                    hierarchy_update.parent_route if hierarchy_update.parent_route is not None else page.parent_route,
                    hierarchy_update.parent_type if hierarchy_update.parent_type is not None else page.parent_type,
                    page_id
                )
                
                if not is_valid:
                    # If still not valid, raise an error
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Could not generate a unique route for page '{page.name}'"
                    )
                    
                # Store the unique route that was generated
                print(f"Generated unique route '{full_route}' for page '{page.name}'")
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
    
    # Update parent route if provided
    if hierarchy_update.parent_route is not None:
        # If parent_route is empty string, set to None
        if hierarchy_update.parent_route == "":
            page.parent_route = None
        else:
            # Check if parent route exists
            parent_page = await Page.get_by_route(db, hierarchy_update.parent_route)
            if not parent_page:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Parent route '{hierarchy_update.parent_route}' not found"
                )
            
            # Check if parent page is marked as a parent
            if not parent_page.is_parent_page:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Page '{parent_page.name}' is not marked as a parent page"
                )
            
            page.parent_route = hierarchy_update.parent_route
            
            # Use the full_route that was validated (which might be a unique route if there was a conflict)
            page.route = full_route
    
    # Update parent type if provided
    if hierarchy_update.parent_type is not None:
        # Validate parent type
        valid_parent_types = ["page", "dashboard", "plugin-studio", "settings", ""]
        if hierarchy_update.parent_type not in valid_parent_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid parent type. Must be one of: {', '.join(valid_parent_types)}"
            )
        
        # If parent_type is empty string, set to None
        if hierarchy_update.parent_type == "":
            page.parent_type = None
        else:
            page.parent_type = hierarchy_update.parent_type
            
            # If parent type is not "page", clear parent_route
            if hierarchy_update.parent_type != "page":
                page.parent_route = None
                
            # Use the full_route that was validated (which might be a unique route if there was a conflict)
            page.route = full_route
    
    # Update is_parent_page if provided
    if hierarchy_update.is_parent_page is not None:
        page.is_parent_page = hierarchy_update.is_parent_page
    
    # Save the updated page
    await page.save(db)
    
    # Convert Page object to PageResponse
    return PageResponse(
        id=page.id,
        name=page.name,
        route=page.route,
        parent_route=page.parent_route,
        parent_type=page.parent_type,
        is_parent_page=page.is_parent_page,
        content=page.content,
        creator_id=page.creator_id,
        is_published=page.is_published,
        created_at=page.created_at,
        updated_at=page.updated_at,
        publish_date=page.publish_date,
        backup_date=page.backup_date,
        description=page.description,
        icon=page.icon,
        navigation_route_id=page.navigation_route_id
    )

@router.get("", response_model=PageListResponse)
async def get_pages(
    skip: int = 0,
    limit: int = 100,
    creator_id: Optional[UUID] = None,
    published_only: bool = False,
    navigation_route_id: Optional[UUID] = None,
    parent_type: Optional[str] = None,
    parent_route: Optional[str] = None,
    is_parent_page: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get a list of pages with optional filtering."""
    # Start with base query
    pages = []
    
    # If creator_id is not provided, use the current user's ID
    if not creator_id:
        creator_id = auth.user_id
        print(f"Using current user ID: {creator_id} for filtering pages")
    
    # Apply filters
    if parent_type:
        # Get pages with a specific parent type and creator
        pages = await Page.get_by_parent_type_and_creator(db, parent_type, creator_id)
        
        # Filter by published status if specified
        if published_only:
            pages = [p for p in pages if p.is_published]
    elif parent_route:
        # Get pages with a specific parent route and creator
        pages = await Page.get_by_parent_route_and_creator(db, parent_route, creator_id)
        
        # Filter by published status if specified
        if published_only:
            pages = [p for p in pages if p.is_published]
    elif is_parent_page is not None:
        # Get parent pages or non-parent pages for a specific creator
        pages = await Page.get_by_is_parent_and_creator(db, is_parent_page, creator_id)
        
        # Filter by published status if specified
        if published_only:
            pages = [p for p in pages if p.is_published]
    elif navigation_route_id:
        # Get pages for a specific navigation route and creator
        nav_route = await NavigationRoute.get_by_id(db, navigation_route_id)
        if not nav_route:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Navigation route not found"
            )
        
        # Get pages for this navigation route and creator
        pages = await Page.get_by_navigation_route_and_creator(db, navigation_route_id, creator_id)
        
        # Filter by published status if specified
        if published_only:
            pages = [p for p in pages if p.is_published]
    elif published_only:
        # Get only published pages for the creator
        pages = await Page.get_published_pages_by_creator(db, creator_id)
    else:
        # Get pages created by the specified user
        pages = await Page.get_by_creator(db, creator_id)
    
    # Apply pagination
    total = len(pages)
    pages = pages[skip : skip + limit]
    
    # Convert Page objects to PageResponse objects
    page_responses = [
        PageResponse(
            id=page.id,
            name=page.name,
            route=page.route,
            parent_route=page.parent_route,
            parent_type=page.parent_type,
            content=page.content,
            creator_id=page.creator_id,
            is_published=page.is_published,
            created_at=page.created_at,
            updated_at=page.updated_at,
            publish_date=page.publish_date,
            backup_date=page.backup_date,
            description=page.description,
            icon=page.icon,
            navigation_route_id=page.navigation_route_id
        )
        for page in pages
    ]
    
    return PageListResponse(pages=page_responses, total=total)

@router.get("/{page_id}", response_model=PageDetailResponse)
async def get_page(
    page_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get a specific page by ID."""
    page = await Page.get_by_id(db, page_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page not found"
        )
    
    # Check if user has access to the page
    # Convert both IDs to strings without hyphens for comparison
    creator_id_str = str(page.creator_id).replace('-', '')
    auth_id_str = str(auth.user_id).replace('-', '')
    
    if creator_id_str != auth_id_str and not page.is_published:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to access this page"
        )
    
    # Convert Page object to PageDetailResponse
    return PageDetailResponse(
        id=page.id,
        name=page.name,
        route=page.route,
        parent_route=page.parent_route,
        parent_type=page.parent_type,
        content=page.content,
        content_backup=page.content_backup,
        creator_id=page.creator_id,
        is_published=page.is_published,
        created_at=page.created_at,
        updated_at=page.updated_at,
        publish_date=page.publish_date,
        backup_date=page.backup_date,
        description=page.description,
        icon=page.icon,
        navigation_route_id=page.navigation_route_id
    )

@router.get("/route/{route}", response_model=PageDetailResponse)
async def get_page_by_route(
    route: str,
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user)
):
    """Get a specific page by route."""
    page = await Page.get_by_route(db, route)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page not found"
        )
    
    # Check if user has access to the page
    if not page.is_published:
        if not auth:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to access this page"
            )
        
        # Convert both IDs to strings without hyphens for comparison
        creator_id_str = str(page.creator_id).replace('-', '')
        auth_id_str = str(auth.user_id).replace('-', '')
        
        if creator_id_str != auth_id_str:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to access this page"
            )
    
    # Convert Page object to PageDetailResponse
    return PageDetailResponse(
        id=page.id,
        name=page.name,
        route=page.route,
        parent_route=page.parent_route,
        parent_type=page.parent_type,
        content=page.content,
        content_backup=page.content_backup,
        creator_id=page.creator_id,
        is_published=page.is_published,
        created_at=page.created_at,
        updated_at=page.updated_at,
        publish_date=page.publish_date,
        backup_date=page.backup_date,
        description=page.description,
        icon=page.icon,
        navigation_route_id=page.navigation_route_id
    )

@router.put("/{page_id}", response_model=PageResponse)
async def update_page(
    page_id: UUID,
    page_data: PageUpdate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Update a specific page."""
    # Log the incoming request data for debugging
    print(f"Updating page with ID: {page_id}")
    print(f"Page data received: {page_data.dict()}")
    print(f"Page data navigation_route_id: {page_data.navigation_route_id}")
    print(f"Page data navigation_route_id type: {type(page_data.navigation_route_id)}")
    
    # Log the raw request body
    import json
    try:
        from fastapi.requests import Request
        from fastapi import Request as FastAPIRequest
        request = FastAPIRequest.scope.get("request")
        body = await request.body()
        print(f"Raw request body: {body.decode()}")
        try:
            json_body = json.loads(body.decode())
            print(f"JSON request body: {json_body}")
            print(f"navigation_route_id in raw JSON: {json_body.get('navigation_route_id')}")
        except:
            print("Could not parse request body as JSON")
    except Exception as e:
        print(f"Error accessing raw request: {str(e)}")
    
    page = await Page.get_by_id(db, page_id)
    print(f"Page found: {page is not None}")
    if page:
        print(f"Page details: id={page.id}, name={page.name}, creator_id={page.creator_id}")
    
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page not found"
        )
    
    # Check if user has permission to update the page
    print(f"Page creator_id: {page.creator_id}, Current user id: {auth.user_id}")
    
    # Convert both IDs to strings without hyphens for comparison
    creator_id_str = str(page.creator_id).replace('-', '')
    auth_id_str = str(auth.user_id).replace('-', '')
    
    print(f"Normalized creator_id: {creator_id_str}")
    print(f"Normalized auth_id: {auth_id_str}")
    print(f"Are they equal? {creator_id_str == auth_id_str}")
    
    if creator_id_str != auth_id_str:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to update this page"
        )
    
    # Check if route is being updated and if it already exists
    if page_data.route and page_data.route != page.route:
        existing_page = await Page.get_by_route(db, page_data.route)
        if existing_page and existing_page.id != page.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A page with this route already exists"
            )
    
    # Check if navigation route exists if provided
    if page_data.navigation_route_id:
        print(f"Navigation route ID provided: {page_data.navigation_route_id}")
        print(f"Navigation route ID type: {type(page_data.navigation_route_id)}")
        nav_route = await NavigationRoute.get_by_id(db, page_data.navigation_route_id)
        print(f"Navigation route found: {nav_route is not None}")
        if nav_route:
            print(f"Navigation route details: id={nav_route.id}, name={nav_route.name}")
            
            # Check if user has access to the navigation route
            print(f"Nav route creator_id: {nav_route.creator_id}, Current user id: {auth.user_id}")
            
            # Convert both IDs to strings without hyphens for comparison
            nav_creator_id_str = str(nav_route.creator_id).replace('-', '')
            auth_id_str = str(auth.user_id).replace('-', '')
            
            print(f"Normalized nav creator_id: {nav_creator_id_str}")
            print(f"Normalized auth_id: {auth_id_str}")
            print(f"Are they equal? {nav_creator_id_str == auth_id_str}")
            
            # For system routes, allow any user to use them
            if nav_route.is_system_route:
                print("This is a system route - allowing access")
            elif nav_creator_id_str != auth_id_str:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have permission to use this navigation route"
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The specified navigation route does not exist"
            )
    else:
        print("No navigation_route_id provided in the update - this is fine, setting to null")
        
    # Update page fields
    if page_data.name is not None and page_data.name != page.name:
        # Name has changed, we need to update the route to keep it in sync with the name
        page.name = page_data.name
        
        # Generate a unique route based on the new name
        # Remove spaces and special characters
        base_route = page_data.name.lower().replace(' ', '-')
        import re
        base_route = re.sub(r'[^a-z0-9-]', '', base_route)
        
        # Add timestamp to ensure uniqueness
        import time
        timestamp = int(time.time())
        unique_route = f"{base_route}-{timestamp}"
        
        # If there's a parent route or parent type, generate the full route
        if page.parent_route or page.parent_type:
            try:
                # Use the existing generate_full_route method but with our unique route as the name
                # We'll temporarily set the name to our unique route
                original_name = page.name
                page.name = unique_route
                page.route = await page.generate_full_route(db)
                # Restore the actual name
                page.name = original_name
            except ValueError as e:
                print(f"Error generating full route: {e}")
                # Fallback to just using the unique route
                page.route = unique_route
        else:
            # No parent, just use the unique route
            page.route = unique_route
            
        print(f"Updated route to {page.route} based on name change to {page.name}")
    elif page_data.route is not None:
        # Only update route directly if name hasn't changed but route was explicitly provided
        page.route = page_data.route
    if page_data.parent_route is not None:
        page.parent_route = page_data.parent_route
    if page_data.content is not None:
        page.content = page_data.content
    if page_data.description is not None:
        page.description = page_data.description
    if page_data.icon is not None:
        page.icon = page_data.icon
    # Handle navigation_route_id explicitly, including setting it to None
    if hasattr(page_data, 'navigation_route_id'):
        if page_data.navigation_route_id is None or page_data.navigation_route_id == "":
            # Explicitly set to None when null or empty string is passed
            print("Explicitly setting navigation_route_id to None")
            page.navigation_route_id = None
        else:
            # Ensure navigation_route_id is stored without hyphens
            print(f"Setting navigation_route_id to {page_data.navigation_route_id}")
            page.navigation_route_id = str(page_data.navigation_route_id).replace('-', '')
    if page_data.is_published is not None:
        page.is_published = page_data.is_published
        if page_data.is_published:
            await page.publish()
    
    await page.save(db)
    
    # Convert Page object to PageResponse
    return PageResponse(
        id=page.id,
        name=page.name,
        route=page.route,
        parent_route=page.parent_route,
        parent_type=page.parent_type,
        content=page.content,
        creator_id=page.creator_id,
        is_published=page.is_published,
        created_at=page.created_at,
        updated_at=page.updated_at,
        publish_date=page.publish_date,
        backup_date=page.backup_date,
        description=page.description,
        icon=page.icon,
        navigation_route_id=page.navigation_route_id
    )

@router.delete("/{page_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_page(
    page_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Delete a specific page."""
    page = await Page.get_by_id(db, page_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page not found"
        )
    
    # Check if user has permission to delete the page
    # Convert both IDs to strings without hyphens for comparison
    creator_id_str = str(page.creator_id).replace('-', '')
    auth_id_str = str(auth.user_id).replace('-', '')
    
    if creator_id_str != auth_id_str:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to delete this page"
        )
    
    # Delete the page
    await db.delete(page)
    await db.commit()
    
    return None

@router.post("/{page_id}/backup", response_model=PageResponse)
async def create_page_backup(
    page_id: UUID,
    backup_data: PageBackup,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Create a backup of a specific page."""
    page = await Page.get_by_id(db, page_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page not found"
        )
    
    # Check if user has permission to backup the page
    # Convert both IDs to strings without hyphens for comparison
    creator_id_str = str(page.creator_id).replace('-', '')
    auth_id_str = str(auth.user_id).replace('-', '')
    
    if creator_id_str != auth_id_str:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to backup this page"
        )
    
    # Create backup
    if backup_data.create_backup:
        await page.create_backup()
        await page.save(db)
    
    # Convert Page object to PageResponse
    return PageResponse(
        id=page.id,
        name=page.name,
        route=page.route,
        parent_route=page.parent_route,
        parent_type=page.parent_type,
        content=page.content,
        creator_id=page.creator_id,
        is_published=page.is_published,
        created_at=page.created_at,
        updated_at=page.updated_at,
        publish_date=page.publish_date,
        backup_date=page.backup_date,
        description=page.description,
        icon=page.icon,
        navigation_route_id=page.navigation_route_id
    )

@router.post("/{page_id}/publish", response_model=PageResponse)
async def publish_page(
    page_id: UUID,
    publish_data: PagePublish,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Publish or unpublish a specific page."""
    page = await Page.get_by_id(db, page_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page not found"
        )
    
    # Check if user has permission to publish/unpublish the page
    # Convert both IDs to strings without hyphens for comparison
    creator_id_str = str(page.creator_id).replace('-', '')
    auth_id_str = str(auth.user_id).replace('-', '')
    
    if creator_id_str != auth_id_str:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to publish/unpublish this page"
        )
    
    # Publish or unpublish
    if publish_data.publish:
        await page.publish()
    else:
        await page.unpublish()
    
    await page.save(db)
    
    # Convert Page object to PageResponse
    return PageResponse(
        id=page.id,
        name=page.name,
        route=page.route,
        parent_route=page.parent_route,
        parent_type=page.parent_type,
        content=page.content,
        creator_id=page.creator_id,
        is_published=page.is_published,
        created_at=page.created_at,
        updated_at=page.updated_at,
        publish_date=page.publish_date,
        backup_date=page.backup_date,
        description=page.description,
        icon=page.icon,
        navigation_route_id=page.navigation_route_id
    )
