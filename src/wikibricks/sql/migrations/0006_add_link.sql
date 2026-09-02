ALTER TABLE curation_patches
    DROP CONSTRAINT curation_patches_operation_check;

ALTER TABLE curation_patches
    ADD CONSTRAINT curation_patches_operation_check CHECK (
        operation IN (
            'create_page',
            'update_page',
            'add_link',
            'retarget_links',
            'add_alias',
            'supersede_page'
        )
    );
